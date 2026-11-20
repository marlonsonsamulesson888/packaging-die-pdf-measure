# 刀模 PDF 测量与盒型识别

从矢量刀模 PDF 自动量出部件、底面、墙高、双压痕、内折墙、天地盖面板压痕中心尺寸差（非装配净间隙）和成盒外尺寸，查找候选出血线，并与声称尺寸比对。只给数据，不判定可生产。

入口：[SKILL.md](SKILL.md)。

## 使用
需要 Python 3.10+ 和 PyMuPDF（`pip install pymupdf`）。

```sh
python scripts/measure_die.py 刀模.pdf --claimed 200x47x235 --md 测量记录.md --json 测量.json
python -m unittest discover -s tests -v
```

测试用脚本现场生成的合成刀模，不含客户文件。设置环境变量 `DIE_PDF=真实刀模.pdf` 可额外跑一次真实文件冒烟测试。

## 限制
- 默认运行仅给候选测量；先人工确认颜色角色，再同时指定 `--cut-color` 与 `--crease-color` 才给成盒近似结论。指定颜色须与输出色值完全一致，未匹配时不自动替换。
- 出血状态为 candidate 或 unknown，不把未检出当成没有出血。脚本参数不是通用工厂标准。
- 只认描边颜色，单色或同色标注的刀模要先人工分层或用参数指定颜色。
- 天地盖/托盘类（底面四周起墙）能自动解读；插盒、管盒、异形盒只给线条清单。
- 曲线按弦长近似；不读纸厚，内尺寸不写死。

## 隐私
本工具只读本地文件、不联网。输出记录含文件名，分享前自查。示例已匿名化。

## 许可
MIT，Copyright (c) 2026 Marlonson Samulesson，见 [LICENSE](LICENSE)。

## 输出兼容说明
`assembly.clearance_mm` 为兼容旧版保留的字段名：width/length 表示两面板压痕中心尺寸差，height 表示墙高差；不是装配净间隙。
`bleed_line.found` 仅表示是否检出候选外扩框，不是出血有无判定；请同时读取 status 和 note。候选存在也仍待人工确认，所以当前 CLI 正常测量通常返回提示码 1，不应把 0 当验收目标。
