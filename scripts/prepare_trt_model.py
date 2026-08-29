#!/usr/bin/env python3
"""ONNX 图手术：把恒定条件的 If 控制流节点内联为其 taken 分支，产出 TRT 友好模型。

背景：torch 动态控制流（DINOv2 注意力掩模的 q_len==k_len 检查等）导出的 If 节点，
其分支输出形状不一致（如 [-1,1536] vs [-1,1,1536]），Jetson 版 onnxruntime 1.24
的 TensorRT EP 在分区时直接抛异常（x86 ORT 1.28 静默回退）。对固定输入尺寸
（518×518），这些条件实测恒为真 → 可以静态内联 then 分支，得到无 If 的等价模型。

用法：
    python prepare_trt_model.py <model.onnx> <out.onnx> [--cond output_name]
    默认把所有 If 内联为 then 分支（条件已用 bottle_probe 验证恒真）。

内联后必须用原模型对比验证输出（CUDA EP 跑同图，score 应完全一致）。
"""
import sys

import onnx
from onnx import helper


def inline_ifs(model_path: str, out_path: str, taken_branch: str = "then"):
    m = onnx.load(model_path, load_external_data=False)
    g = m.graph

    used = set()
    for n in g.node:
        used.update(list(n.input))
        used.update(list(n.output))
    for init in g.initializer:
        used.add(init.name)

    def unique(base: str) -> str:
        i = 0
        name = base
        while name in used:
            name = f"{base}_inline_{i}"
            i += 1
        used.add(name)
        return name

    new_nodes = []
    n_inlined = 0
    for n in g.node:
        if n.op_type != "If":
            new_nodes.append(n)
            continue

        # ONNX 规范：attribute 顺序 then_branch(0)、else_branch(1)
        branch = n.attribute[0].g if taken_branch == "then" else n.attribute[1].g
        # 分支形式参数 → If 实际输入（If 的第一个输入是 cond，其余按序对应）
        formal_in = [i.name for i in branch.input]
        actual_in = list(n.input)[1:]
        inp_map = dict(zip(formal_in, actual_in))
        out_map = {o.name: n.output[k] for k, o in enumerate(branch.output)}

        # 分支内部节点输出重命名（避免与主图重名）
        renamed = {}
        for sn in branch.node:
            for o in sn.output:
                renamed[o] = unique(o)

        for sn in branch.node:
            new_in = []
            for i in sn.input:
                if i in inp_map:
                    new_in.append(inp_map[i])
                elif i in renamed:
                    new_in.append(renamed[i])
                elif i in out_map:
                    new_in.append(out_map[i])
                else:
                    # 外层作用域引用（initializer / 主图节点输出），保持原名
                    new_in.append(i)
            new_out = [out_map.get(o, renamed.get(o, o)) for o in sn.output]
            new_nodes.append(helper.make_node(
                sn.op_type, new_in, new_out,
                name=f"{sn.name}_inlined",
                domain=sn.domain or None,
                **( {a.name: helper.get_attribute_value(a) for a in sn.attribute} )
            ))
        n_inlined += 1

    del g.node[:]
    g.node.extend(new_nodes)
    onnx.save(m, out_path)
    print(f"[prepare_trt] 内联 {n_inlined} 个 If 节点 → {out_path}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python prepare_trt_model.py <in.onnx> <out.onnx> [--branch then|else]")
        sys.exit(1)
    branch = "then"
    if "--branch" in sys.argv:
        branch = sys.argv[sys.argv.index("--branch") + 1]
    inline_ifs(sys.argv[1], sys.argv[2], branch)
