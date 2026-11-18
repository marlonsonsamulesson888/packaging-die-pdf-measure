# 刀模 PDF 测量与盒型识别

从矢量刀模 PDF 自动量出部件、底面、墙高、双压痕、内折墙、天地盖配合量和成盒外尺寸，检查出血线，并与声称尺寸比对。只给数据，不判定可生产。

入口：[SKILL.md](SKILL.md)。

## 使用
需要 Python 3.10+ 和 PyMuPDF（`pip install pymupdf`）。

```sh
python scripts/measure_die.py 刀模.pdf --claimed 200x47x235 --md 测量记录.md --json 测量.json
python -m unittest discover -s tests -v
```

测试用脚本现场生成的合成刀模，不含客户文件。设置环境变量 `DIE_PDF=真实刀模.pdf` 可额外跑一次真实文件冒烟测试。

## 限制
- 只认描边颜色，单色或同色标注的刀模要先人工分层或用参数指定颜色。
- 天地盖/托盘类（底面四周起墙）能自动解读；插盒、管盒、异形盒只给线条清单。
- 曲线按弦长近似；不读纸厚，内尺寸不写死。

## 隐私
本工具只读本地文件、不联网。输出记录含文件名，分享前自查。示例已匿名化。

## 许可
MIT，Copyright (c) 2026 Marlonson Samulesson，见 [LICENSE](LICENSE)。
