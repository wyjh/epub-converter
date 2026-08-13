#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成本地测试输入：UTF-8/GBK 两种编码的 TXT、同名元信息、封面图片。"""

from __future__ import annotations

from pathlib import Path


def make_inputs(root: Path) -> None:
    inp = root / "input"
    inp.mkdir(parents=True, exist_ok=True)

    utf8 = """没有名字的书
作者：测试作者

第一章 开篇
　　这是一段正常的正文内容，用来验证首行缩进是否保留。
　　第二段同样以全角空格开头。
（本章未完，请点击下一页继续阅读）
https://www.example.com/ads

　　第三段前面有一个多余的广告行，应该被清洗掉。
　　结尾一句没有句号就换行了
这是被硬换行拆开的后半句，应该被合并。

第二章 发展
　　剧情继续推进，这一段没有任何问题。
　　对话示例：“你来了？”
　　“嗯，我来了。”

第三章
　　第三章没有标题，只有徽标。

Chapter 4 Test
　　English style chapter heading should be normalized.

尾声
　　故事到此结束。
"""
    (inp / "样例书.txt").write_text(utf8, encoding="utf-8")

    meta = """# 元信息示例（与 TXT 同名）
title: 测试之书
author: 测试作者
description: |
  这是一本用于验证转换流水线的测试书。

  简介第二段，说明书籍主题与风格。
tags:
  - 测试
  - 科幻
  - 小说
publisher: 测试出版社
series: 测试系列
series_index: 1
lang: zh
"""
    (inp / "样例书.yaml").write_text(meta, encoding="utf-8")

    gbk_text = """GBK编码的书

序章 起点
　　这是用 GBK 编码保存的文本，用于验证编码自动检测。
　　包含中文标点：，。！？

第七章 中间
　　第七章使用中文数字编号，应被规范为阿拉伯数字。
"""
    (inp / "GBK样例.txt").write_bytes(gbk_text.encode("gb18030"))

    try:
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (600, 900), (30, 40, 90))
        d = ImageDraw.Draw(im)
        d.rectangle([60, 120, 540, 780], fill=(220, 200, 150))
        d.text((140, 420), "TEST", fill=(30, 30, 30))
        im.save(inp / "样例书.png", format="PNG")
        im.save(inp / "GBK样例.jpg", format="JPEG", quality=90)
    except ImportError:
        print("Pillow 不可用，跳过封面生成")

    print(f"测试输入已生成：{inp}")


if __name__ == "__main__":
    make_inputs(Path(__file__).resolve().parent)
