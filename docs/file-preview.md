# 工作区文件预览

目录树单击文件会打开中间的「文件预览」页签。再次点击其它文件替换当前预览；关闭预览返回此前的视图，并保留聊天草稿与阅读位置。预览独立于对话，查看文件不需要启动 OpenCode 服务。目录仍通过点击展开。

## 操作

- 代码与文本：语法高亮、行号、自动换行、复制当前显示内容，以及 Ctrl/Cmd+F 查找。查找按字面内容匹配，不区分大小写；Enter / Shift+Enter 切换下一个 / 上一个结果，最多定位 1,000 个结果。
- Markdown：默认阅读排版，可切换源码。沿用工作区已有的安全 Markdown 渲染器；原始 HTML 显示为文本。
- 图片：支持 PNG/APNG、JPEG、GIF、WebP、SVG、ICO、BMP、AVIF，默认适应窗口，可以缩放或恢复 100%。浏览器无法解码时显示清晰提示。
- PDF：使用随包 PDF.js 展示单页，支持页码跳转、上一页/下一页、左右方向键、适应宽度、缩放和文字选择。需要密码的 PDF 提示使用系统应用打开。
- 通用操作：刷新当前文件、引用到对话、放大预览、用系统应用打开。Escape 依次关闭查找、恢复预览大小、关闭预览。右键菜单保留系统打开与文件管理器定位。

预览是只读的，不提供编辑或保存按钮。不支持的格式、损坏文件和超过限制的文件保留系统打开入口。窄屏打开文件后自动收起目录树。

## 接口与读取限制

- `GET /workspace/projects/{id}/entries/preview?path=...`：返回类型、大小、版本及文本内容。
- `GET /workspace/projects/{id}/entries/content?path=...&version=...`：只返回图片或 PDF 字节。版本改变返回 409，提示刷新。

两个接口沿用注册项目路径解析，不接受绝对路径、穿越路径或指向项目外部的符号链接。只读取普通文件；按上限读取，读取时再次检查文件类型和版本。文本支持 UTF-8、带 BOM 的 UTF-8/UTF-16 和 GB18030，统一换行但不写回文件。

文本上限 2 MiB、图片 32 MiB、PDF 64 MiB；文本显示前 20,000 行，超过时显示截断提示。超过 200,000 字符的源码按纯文本展示，以避免同步高亮阻塞界面。图片/PDF 经由无缓存、带 `nosniff` 与沙箱 CSP 的媒体接口读取。项目 HTML 不会作为网页执行，SVG 以图片元素展示，PDF 禁用动态求值。

## 离线资源与打包

Prism 1.30.0（MIT）及 PDF.js 5.4.624（Apache-2.0）固定在 `sona_code/web/static/vendor/file-preview.json`，资源与许可证随源码分发，运行时不访问 CDN。PDF.js 的 worker、CMap、标准字体和 WASM 一并打包，新增静态资源约 4.8 MiB。

重建资源：

```sh
python scripts/prepare-preview-assets.py
```

脚本只使用 Python 标准库，从官方 npm registry 获取固定版本，并校验预设 SHA-512 integrity。已下载的包复用 `build/preview-assets` 缓存；输出清单记录资源 SHA-256。无前端 npm 安装步骤。升级时同时调整脚本的版本、integrity 与资源测试，并重新验证浏览器和 Electron 中的 PDF 展示。

`pyproject.toml` 显式包含 PDF.js 子目录；`packaging/sidecar.spec` 已递归包含整个静态资源目录。Windows 发布仍需按照原有流程先重建 sidecar 再打包 Electron，并执行原生安装包/绿色版冒烟验证。

## 验证

```sh
.venv/bin/python -m pytest tests/test_workspace_file_preview.py tests/test_workspace_file_preview_frontend.py -q
.venv/bin/python -m pytest tests/ -q
```

覆盖编码、类型识别、边界限制、版本变化、路径/符号链接/特殊文件拒绝、字面查找、预览期间聊天 DOM 保留、返回阅读位置及离线资源完整性。浏览器还应验证图片/PDF 解码、翻页、查找、草稿保留、文件引用、放大/恢复和窄屏布局。
