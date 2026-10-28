# -*- coding: utf-8 -*-
"""规范化 EDF+ 注释通道的头部，不修改脑电量程、全局元信息或数据记录。

EDF+ §2.2.1 要求注释通道的 label 为 ``EDF Annotations``，数字范围为
-32768/32767，物理范围的两端不相等；transducer、physical dimension、
prefilter 和 reserved 字段均应填空格。MNE/edfio 导出的空单位本来是正确的。
https://www.edfplus.info/specs/edfplus.html

本函数只处理这些注释字段，不是完整的 EDF 合规检查器。
"""

from __future__ import annotations

import math
import os

HDR = 256


def patch_edf_header(edf_path: str, dialect: str = "EDF+C") -> dict:
    """修正注释通道头；不把连续/不连续记录相互转换。"""
    if dialect not in {"EDF+C", "EDF+D"}:
        raise ValueError(f"不支持的 EDF+ 类型: {dialect}")
    info: dict = {"patched": [], "skipped": []}
    if not os.path.exists(edf_path):
        info["skipped"].append("文件不存在")
        return info

    with open(edf_path, "r+b") as fh:
        head = fh.read(HDR)
        if len(head) != HDR or head[:8] != b"0       ":
            raise ValueError("不是有效的 EDF 文件头")
        n_sig = int(head[252:256])
        n_hdr = int(head[184:192])
        if n_sig <= 0 or n_hdr != HDR * (n_sig + 1):
            raise ValueError("EDF 信号数与头部长度不一致")
        if not head[192:236].startswith(dialect.encode("ascii")):
            raise ValueError(f"文件类型与 {dialect} 不一致，不能只改头部转换类型")
        header = bytearray(head + fh.read(n_hdr - HDR))
        if len(header) != n_hdr:
            raise ValueError("EDF 信号头不完整")

        fields = {}
        offset = HDR
        for name, width in (
            ("label", 16), ("transducer", 80), ("physical dimension", 8),
            ("physical minimum", 8), ("physical maximum", 8),
            ("digital minimum", 8), ("digital maximum", 8),
            ("prefilter", 80), ("samples", 8), ("reserved", 32),
        ):
            fields[name] = (offset, width)
            offset += width * n_sig

        def read_field(name, i):
            base, width = fields[name]
            return bytes(header[base + i * width:base + (i + 1) * width])

        def set_field(name, i, text):
            base, width = fields[name]
            value = text.encode("ascii")
            if len(value) > width:
                raise ValueError(f"{name} 超出 {width} 字节，拒绝截断")
            value = value.ljust(width, b" ")
            old = read_field(name, i)
            if old != value:
                header[base + i * width:base + (i + 1) * width] = value
                info["patched"].append(f"ann[{i}].{name} {old!r} -> {value!r}")

        ann_indices = [i for i in range(n_sig)
                       if read_field("label", i).strip() == b"EDF Annotations"]
        if not ann_indices:
            info["skipped"].append("没有 'EDF Annotations' 通道")
            return info

        for i in ann_indices:
            for name in ("transducer", "physical dimension", "prefilter", "reserved"):
                set_field(name, i, "")
            set_field("digital minimum", i, "-32768")
            set_field("digital maximum", i, "32767")
            try:
                pmin = float(read_field("physical minimum", i))
                pmax = float(read_field("physical maximum", i))
                valid_range = math.isfinite(pmin) and math.isfinite(pmax) and pmin != pmax
            except ValueError:
                valid_range = False
            if not valid_range:
                set_field("physical minimum", i, "-1")
                set_field("physical maximum", i, "1")

        if info["patched"]:
            fh.seek(0)
            fh.write(header)
    return info
