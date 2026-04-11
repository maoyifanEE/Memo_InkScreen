# EPD 图片转数组工具

这是一个为你当前 Good Display 400x300 示例工程准备的 Python 小工具。

## 已实现

- 图片加载
- 输入图片预览
- 输出图片预览（黑白 / 4 灰）
- 400x300 预设
- 黑白全屏数组导出
- 黑白局部刷新数组导出
- 4 灰全屏数组导出
- 4 灰调试位面导出说明（plane24 / plane26）
- C 数组文本复制 / 保存

## 目录结构

- `app.py`：程序入口
- `core/models.py`：数据结构
- `core/presets.py`：墨水屏预设
- `core/image_pipeline.py`：图片处理 / 打包 / 转换主逻辑
- `core/export_c.py`：C 数组文本导出
- `ui/main_window.py`：Tkinter UI

## 运行

```bash
cd epd_image_tool
pip install -r requirements.txt
python app.py
```

## 和你当前示例的对应关系

- 黑白全屏：导出的数组可以对应 `EPD_WhiteScreen_ALL()`
- 黑白局刷：导出的数组可以对应 `EPD_Dis_Part(x, y, data, height, width)`
- 4 灰全屏：导出的主数组可以对应 `EPD_WhiteScreen_ALL_4G()`

## 当前约束

- 4 灰局刷：当前示例驱动没有直接接口，所以这里先不导出
- 局刷按字节对齐：x 和 width 会自动按 8 像素对齐
- 4 灰预览现在采用 4 档阈值量化：黑 / 深灰 / 浅灰 / 白

## 后续适合继续加的功能

- 更多墨水屏型号预设
- 局刷区域框选
- 文字叠加 / 时间模板
- 导出 `.bin`
- 直接生成 `.h + .cpp`
- 预览叠加 ghosting / 刷新策略说明
