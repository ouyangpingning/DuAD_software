/*
 * bayer_demosaic.c — 双线性 Bayer 去马赛克（Jetson arm64 专用加速）。
 *
 * 背景：arm64 版大恒 Galaxy SDK 不带 DxImageProc 库，libgxiapi.so 也不
 * 导出 DxRaw8toRGB24。numpy 纯实现 5MP 约 120ms，会把相机回调线程拖慢
 * （限制到 ~8fps）。本文件提供与 DxImageProc NEIGHBOUR 一致的双线性
 * 插值，5MP 约 3ms。
 *
 * 编译（在 Jetson 上）：
 *   gcc -shared -fPIC -O2 -o libbayer_demosaic.so bayer_demosaic.c
 * 产出放到 backend/libs_arm64/；camera.py 优先 ctypes 加载，失败时回退
 * numpy 实现（_bayer_demosaic_numpy）。
 *
 * bayer 参数沿用 DxPixelColorFilter 枚举（Linux 语义）：
 *   1=RG(RGGB) 2=GB(GBRG) 3=GR(GRBG) 4=BG(BGGR)
 * 输出 RGB888（interleaved，与 DxRaw8toRGB24 输出一致）。
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* 以 RGGB 规范相位描述某个颜色通道的已知采样偏移。
 * 规范：R 在 (0,0)，G 在 (0,1)/(1,0)，B 在 (1,1)。 */
typedef struct { int oy, ox; } Phase;

/* 已知采样在 (oy,ox) 相位、步长 2，双线性补全到全分辨率。
 * src: 该相位的采样（h2 x w2，uint8），dst: 全分辨率输出（uint16）。 */
static void fill_channel(const uint8_t *restrict src, uint16_t *restrict dst,
                         int h, int w, int h2, int w2, int oy, int ox)
{
    /* 边缘复制 padding 后的采样：ps[i+1][j+1] = src[i][j] */
    int pw = w2 + 2, ph = h2 + 2;
    uint16_t *ps = (uint16_t *)malloc((size_t)pw * ph * sizeof(uint16_t));
    if (!ps) return;
    /* 填充内部区域 */
    for (int i = 0; i < h2; ++i) {
        for (int j = 0; j < w2; ++j)
            ps[(i + 1) * pw + (j + 1)] = src[i * w2 + j];
    }
    /* 上下左右边缘复制（四个角由边延伸自然覆盖） */
    for (int j = 0; j < w2; ++j) {
        ps[j + 1] = ps[pw + (j + 1)];                          /* 上边 */
        ps[(ph - 1) * pw + (j + 1)] = ps[(ph - 2) * pw + (j + 1)]; /* 下边 */
    }
    for (int i = 0; i < ph; ++i) {
        ps[i * pw] = ps[i * pw + 1];                           /* 左边 */
        ps[i * pw + (pw - 1)] = ps[i * pw + (pw - 2)];         /* 右边 */
    }

    /* 逐输出像素填充。ps 行/列 = 相位网格坐标 + 1（padding）。
     * 已知采样：src[py][px] = ps[py+1][px+1]
     * 垂直邻（x ≡ ox 且 y ≢ oy）：src[py-oy][px]、src[py+1-oy][px]
     *   → ps 行 py+1-oy / py+2-oy、列 px+1
     * 水平邻（y ≡ oy 且 x ≢ ox）：src[py][px-ox]、src[py][px+1-ox]
     *   → ps 行 py+1、列 px+1-ox / px+2-ox
     * 对角邻（均异）：上两行两列组合
     */
    for (int y = 0; y < h; ++y) {
        int py = y / 2;
        int same = ((y - oy) & 1) == 0;
        int r1 = same ? (py + 1) : (py + 1 - oy);
        int r2 = same ? (py + 1) : (py + 2 - oy);
        int row_off = y * w;
        for (int x = 0; x < w; ++x) {
            int px = x / 2;
            uint16_t v;
            if (((x - ox) & 1) == 0) {
                v = ps[r1 * pw + (px + 1)];                     /* 已知/垂直 */
            } else {
                int c1 = px + 1 - ox, c2 = px + 2 - ox;
                uint16_t a = ps[r1 * pw + c1];
                uint16_t b = ps[r1 * pw + c2];
                if (same) {
                    v = (a + b) >> 1;                           /* 水平 */
                } else {
                    uint16_t c = ps[r2 * pw + c1];
                    uint16_t d = ps[r2 * pw + c2];
                    v = (a + b + c + d) >> 2;                   /* 对角 */
                }
            }
            dst[row_off + x] = v;
        }
    }
    free(ps);
}

/* bayer: 1=RG 2=GB 3=GR 4=BG（DxPixelColorFilter，Linux 语义） */
int bayer_demosaic_rgb24(const uint8_t *restrict raw, uint8_t *restrict rgb,
                         int w, int h, int bayer)
{
    if (!raw || !rgb || w < 2 || h < 2 || w % 2 || h % 2 || bayer < 1 || bayer > 4)
        return -1;

    int w2 = w / 2, h2 = h / 2;

    /* 按 Bayer 排列拆分 2x2 相位（RGGB 规范命名） */
    const uint8_t *r00 = NULL, *g01 = NULL, *g10 = NULL, *b11 = NULL;
    /* 相位缓冲区（复制一次，供 fill_channel 使用） */
    uint8_t *buf = (uint8_t *)malloc((size_t)h2 * w2 * 4);
    if (!buf) return -1;
    uint8_t *b_r00 = buf, *b_g01 = buf + h2 * w2;
    uint8_t *b_g10 = buf + 2 * (size_t)h2 * w2, *b_b11 = buf + 3 * (size_t)h2 * w2;

    for (int i = 0; i < h2; ++i) {
        const uint8_t *row0 = raw + (size_t)(2 * i) * w;
        const uint8_t *row1 = raw + (size_t)(2 * i + 1) * w;
        for (int j = 0; j < w2; ++j) {
            b_r00[i * w2 + j] = row0[2 * j];
            b_g01[i * w2 + j] = row0[2 * j + 1];
            b_g10[i * w2 + j] = row1[2 * j];
            b_b11[i * w2 + j] = row1[2 * j + 1];
        }
    }

    switch (bayer) {
        case 1: r00 = b_r00; g01 = b_g01; g10 = b_g10; b11 = b_b11; break;
        case 4: r00 = b_b11; g01 = b_g01; g10 = b_g10; b11 = b_r00; break;
        case 2: r00 = b_g10; g01 = b_r00; g10 = b_b11; b11 = b_g01; break;
        case 3: r00 = b_g01; g01 = b_r00; g10 = b_b11; b11 = b_g10; break;
        default: free(buf); return -1;
    }

    uint16_t *R = (uint16_t *)malloc((size_t)h * w * sizeof(uint16_t));
    uint16_t *G = (uint16_t *)malloc((size_t)h * w * sizeof(uint16_t));
    uint16_t *G2 = (uint16_t *)malloc((size_t)h * w * sizeof(uint16_t));
    uint16_t *B = (uint16_t *)malloc((size_t)h * w * sizeof(uint16_t));
    if (!R || !G || !G2 || !B) { free(buf); free(R); free(G); free(G2); free(B); return -1; }

    fill_channel(r00, R, h, w, h2, w2, 0, 0);
    fill_channel(g01, G, h, w, h2, w2, 0, 1);
    fill_channel(g10, G2, h, w, h2, w2, 1, 0);
    fill_channel(b11, B, h, w, h2, w2, 1, 1);

    /* 合成 RGB888：G = 两个 G 相位的均值 */
    for (int i = 0; i < h * w; ++i) {
        rgb[3 * i]     = (uint8_t)R[i];
        rgb[3 * i + 1] = (uint8_t)((G[i] + G2[i]) >> 1);
        rgb[3 * i + 2] = (uint8_t)B[i];
    }

    free(buf);
    free(R); free(G); free(G2); free(B);
    return 0;
}
