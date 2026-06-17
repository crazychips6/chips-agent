"""known-good 索引 — 预置已知 CLI 工具和 pip 包的描述及调用方式

所有条目在模块加载时构建。检索流程按已知索引 → GitHub → 自己写兜底 的优先级进行。"""

from quick_app.models import KnownGoodEntry, SourceType


# ── Known-Good CLI 工具 ──

CLI_TOOLS: list[KnownGoodEntry] = [
    KnownGoodEntry(
        keywords=["视频", "转码", "gif", "ffmpeg", "音频", "格式转换", "压缩"],
        source_type=SourceType.CLI_TOOL,
        name="ffmpeg",
        description="视频/音频处理工具，支持转码、剪辑、GIF 生成、格式转换、压缩",
        install_hint="sudo apt install ffmpeg 或 brew install ffmpeg",
        params_template=[
            {"name": "input", "type": "string", "description": "输入文件路径"},
            {"name": "output", "type": "string", "description": "输出文件路径"},
        ],
        fixed_params=[
            "ffmpeg", "-i", "{input}",
            # LLM 只能在值位置填空，参数名写死
        ],
        handler_pattern=(
            'result = subprocess.run(\n'
            '    ["ffmpeg", "-i", args["input"], output_path, "-y"],\n'
            '    capture_output=True, text=True, timeout=120,\n'
            ')\n'
            'if result.returncode != 0:\n'
            '    return f"转换失败：{result.stderr[:500]}"\n'
            'return f"已生成 {output_path}"'
        ),
        scene_tags=["video", "audio", "gif", "transcoding"],
        weak_scene_tags=[],
    ),
    KnownGoodEntry(
        keywords=["文档", "转换", "markdown", "pdf", "word", "pandoc", "格式转换"],
        source_type=SourceType.CLI_TOOL,
        name="pandoc",
        description="通用文档格式转换工具，支持 Markdown ↔ PDF/Word/HTML/LaTeX 等互转",
        install_hint="sudo apt install pandoc 或 brew install pandoc",
        params_template=[
            {"name": "input", "type": "string", "description": "输入文件路径"},
            {"name": "from_format", "type": "string", "description": "输入格式（如 markdown, html, docx）"},
            {"name": "to_format", "type": "string", "description": "输出格式（如 pdf, docx, html, latex）"},
            {"name": "output", "type": "string", "description": "输出文件路径"},
        ],
        fixed_params=[
            "pandoc", "{input}", "-f", "{from_format}", "-t", "{to_format}", "-o", "{output}",
        ],
        handler_pattern=(
            'output = args.get("output", os.path.splitext(args["input"])[0] + "." + args["to_format"])\n'
            'result = subprocess.run(\n'
            '    ["pandoc", args["input"],\n'
            '     "-f", args["from_format"],\n'
            '     "-t", args["to_format"],\n'
            '     "-o", output],\n'
            '    capture_output=True, text=True, timeout=60,\n'
            ')\n'
            'if result.returncode != 0:\n'
            '    return f"转换失败：{result.stderr[:500]}"\n'
            'return f"已生成 {output}"'
        ),
        scene_tags=["document", "conversion", "markdown", "pdf"],
        weak_scene_tags=["复杂排版"],
    ),
    KnownGoodEntry(
        keywords=["下载", "视频", "音频", "yt-dlp", "youtube", "bilibili"],
        source_type=SourceType.CLI_TOOL,
        name="yt-dlp",
        description="视频/音频下载工具，支持 YouTube、Bilibili 等数百个网站",
        install_hint="pip install yt-dlp 或 brew install yt-dlp",
        params_template=[
            {"name": "url", "type": "string", "description": "视频链接"},
            {"name": "format", "type": "string", "description": "下载格式，可选 mp4/mp3/best (默认 best)"},
            {"name": "output_dir", "type": "string", "description": "保存目录（默认当前目录）"},
        ],
        fixed_params=[
            "yt-dlp", "{url}",
        ],
        handler_pattern=(
            'output_dir = args.get("output_dir", ".")\n'
            'fmt = args.get("format", "best")\n'
            'cmd = ["yt-dlp", args["url"], "-o", f"{output_dir}/%(title)s.%(ext)s"]\n'
            'if fmt == "mp3":\n'
            '    cmd.extend(["-x", "--audio-format", "mp3"])\n'
            'elif fmt == "mp4":\n'
            '    cmd.extend(["-f", "mp4"])\n'
            'result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)\n'
            'if result.returncode != 0:\n'
            '    return f"下载失败：{result.stderr[:500]}"\n'
            'return f"下载完成，已保存到 {output_dir}"'
        ),
        scene_tags=["download", "video", "audio", "youtube"],
        weak_scene_tags=[],
    ),
    KnownGoodEntry(
        keywords=["ocr", "文字识别", "图片文字", "tesseract", "图像文字"],
        source_type=SourceType.CLI_TOOL,
        name="tesseract",
        description="OCR 文字识别工具，从图片中提取文字",
        install_hint="sudo apt install tesseract-ocr tesseract-ocr-chi-sim 或 brew install tesseract",
        params_template=[
            {"name": "image", "type": "string", "description": "图片文件路径"},
            {"name": "lang", "type": "string", "description": "语言（如 chi_sim, eng，默认 chi_sim+eng）"},
        ],
        fixed_params=[
            "tesseract", "{image}", "stdout", "-l", "{lang}",
        ],
        handler_pattern=(
            'lang = args.get("lang", "chi_sim+eng")\n'
            'result = subprocess.run(\n'
            '    ["tesseract", args["image"], "stdout", "-l", lang],\n'
            '    capture_output=True, text=True, timeout=60,\n'
            ')\n'
            'if result.returncode != 0:\n'
            '    return f"识别失败：{result.stderr[:500]}"\n'
            'return result.stdout.strip()'
        ),
        scene_tags=["ocr", "text-recognition"],
        weak_scene_tags=["表格提取"],  # 表格提取推荐 camelot-py
    ),
    KnownGoodEntry(
        keywords=["天气", "weather", "wttr", "天气预报", "气温"],
        source_type=SourceType.CLI_TOOL,
        name="wttr.in",
        description="命令行天气查询工具，通过 curl 获取天气数据，支持 JSON 格式输出",
        install_hint="需要 curl（通常系统已安装）：curl wttr.in/{city}?format=j1",
        params_template=[
            {"name": "city", "type": "string", "description": "城市名（如 Beijing, London, 中文也支持）"},
            {"name": "days", "type": "integer", "description": "预报天数（1-7，默认 3）"},
        ],
        handler_pattern=(
            'city = args.get("city", "Beijing")\n'
            'days = args.get("days", 3)\n'
            'result = subprocess.run(\n'
            '    ["curl", "-s", "wttr.in/" + city + "?format=j1"],\n'
            '    capture_output=True, text=True, timeout=15,\n'
            ')\n'
            'if result.returncode != 0:\n'
            '    return "查询失败：无法连接 wttr.in"\n'
            'try:\n'
            '    import json\n'
            '    data = json.loads(result.stdout)\n'
            '    weather = data.get("weather", [])\n'
            '    lines = ["🌤 " + city + " 天气预报："]\n'
            '    for day in weather[:days]:\n'
            '        date = day.get("date", "")\n'
            '        temp = day.get("avgtempC", "?")\n'
            '        desc = day.get("hourly", [{}])[0].get("weatherDesc", [{}])[0].get("value", "?")\n'
            '        lines.append(f"  {date}: {desc} {temp}C")\n'
            '    return "\\n".join(lines)\n'
            'except Exception as e:\n'
            '    return f"解析失败：{e}"'
        ),
        scene_tags=["weather", "forecast", "weather"],
        weak_scene_tags=[],
    ),
    KnownGoodEntry(
        keywords=["图片", "缩放", "格式转换", "imagemagick", "convert", "图像处理"],
        source_type=SourceType.CLI_TOOL,
        name="ImageMagick",
        description="图像处理工具，支持缩放、裁剪、格式转换、添加文字等",
        install_hint="sudo apt install imagemagick 或 brew install imagemagick",
        params_template=[
            {"name": "input", "type": "string", "description": "输入图片路径"},
            {"name": "output", "type": "string", "description": "输出路径"},
            {"name": "resize", "type": "string", "description": "缩放尺寸（如 800x600），可选"},
        ],
        fixed_params=[
            "convert", "{input}",
        ],
        handler_pattern=(
            'cmd = ["convert", args["input"]]\n'
            'if "resize" in args:\n'
            '    cmd.extend(["-resize", args["resize"]])\n'
            'cmd.append(args["output"])\n'
            'result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)\n'
            'if result.returncode != 0:\n'
            '    return f"处理失败：{result.stderr[:500]}"\n'
            'return f"已生成 {args["output"]}"'
        ),
        scene_tags=["image", "convert", "resize", "thumbnail"],
        weak_scene_tags=["复杂滤镜"],
    ),
    KnownGoodEntry(
        keywords=["压缩", "归档", "zip", "tar", "gz", "7z", "解压"],
        source_type=SourceType.CLI_TOOL,
        name="系统压缩工具",
        description="文件压缩与解压，支持 zip/tar.gz/7z 等格式",
        install_hint="系统自带或 sudo apt install zip unzip p7zip",
        params_template=[
            {"name": "action", "type": "string", "description": "操作：compress 或 extract"},
            {"name": "input", "type": "string", "description": "输入文件/目录路径"},
            {"name": "output", "type": "string", "description": "输出路径（可选）"},
            {"name": "format", "type": "string", "description": "压缩格式：zip/tar.gz/7z（默认 zip）"},
        ],
        fixed_params=[],
        handler_pattern=(
            'action = args["action"]\n'
            'input_path = args["input"]\n'
            'fmt = args.get("format", "zip")\n'
            'if action == "compress":\n'
            '    output = args.get("output", input_path + "." + fmt)\n'
            '    if fmt == "zip":\n'
            '        cmd = ["zip", "-r", output, input_path]\n'
            '    elif fmt == "tar.gz":\n'
            '        cmd = ["tar", "-czf", output, input_path]\n'
            '    else:\n'
            '        cmd = ["7z", "a", output, input_path]\n'
            'else:\n'
            '    output = args.get("output", ".")\n'
            '    if input_path.endswith(".zip"):\n'
            '        cmd = ["unzip", input_path, "-d", output]\n'
            '    elif input_path.endswith(".tar.gz"):\n'
            '        cmd = ["tar", "-xzf", input_path, "-C", output]\n'
            '    else:\n'
            '        cmd = ["7z", "x", input_path, f"-o{output}"]\n'
            'result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)\n'
            'if result.returncode != 0:\n'
            '    return f"操作失败：{result.stderr[:300]}"\n'
            'return f"已完成 {action}：{output}"'
        ),
        scene_tags=["compress", "extract", "archive"],
        weak_scene_tags=[],
    ),
]


# ── Known-Good Pip 包 ──

PIP_PACKAGES: list[KnownGoodEntry] = [
    KnownGoodEntry(
        keywords=["网页", "爬虫", "抓取", "requests", "http", "api"],
        source_type=SourceType.PIP_PACKAGE,
        name="requests",
        description="HTTP 请求库，用于访问网页和 API",
        install_hint="pip install requests",
        params_template=[
            {"name": "url", "type": "string", "description": "请求 URL"},
            {"name": "method", "type": "string", "description": "请求方法：GET/POST（默认 GET）"},
        ],
        handler_pattern=(
            'import requests\n'
            'url = args["url"]\n'
            'method = args.get("method", "GET")\n'
            'resp = requests.request(method, url, timeout=30)\n'
            'resp.raise_for_status()\n'
            'return resp.text[:5000]'
        ),
        scene_tags=["web", "http", "api", "crawl"],
        weak_scene_tags=[],
    ),
    KnownGoodEntry(
        keywords=["html", "解析", "网页", "beautifulsoup", "soup", "爬虫"],
        source_type=SourceType.PIP_PACKAGE,
        name="beautifulsoup4",
        description="HTML/XML 解析库，从网页中提取结构化数据",
        install_hint="pip install beautifulsoup4",
        params_template=[
            {"name": "html", "type": "string", "description": "HTML 内容"},
            {"name": "selector", "type": "string", "description": "CSS 选择器（如 h1.title）"},
            {"name": "attribute", "type": "string", "description": "提取的属性名（如 text/href/src，默认 text）"},
        ],
        handler_pattern=(
            'from bs4 import BeautifulSoup\n'
            'soup = BeautifulSoup(args["html"], "html.parser")\n'
            'attr = args.get("attribute", "text")\n'
            'elements = soup.select(args["selector"])\n'
            'if attr == "text":\n'
            '    return "\\n".join(el.get_text(strip=True) for el in elements)\n'
            'return "\\n".join(el.get(attr, "") for el in elements)'
        ),
        scene_tags=["html", "parse", "scrape", "crawl"],
        weak_scene_tags=[],
    ),
    KnownGoodEntry(
        keywords=["图片", "处理", "pillow", "pil", "图像", "缩略图", "水印"],
        source_type=SourceType.PIP_PACKAGE,
        name="Pillow",
        description="图像处理库，支持缩放、裁剪、滤镜、格式转换、添加水印",
        install_hint="pip install Pillow",
        params_template=[
            {"name": "input", "type": "string", "description": "输入图片路径"},
            {"name": "output", "type": "string", "description": "输出路径"},
            {"name": "operation", "type": "string", "description": "操作类型：resize/crop/thumbnail/watermark"},
            {"name": "params", "type": "object", "description": "操作参数（如 {\"width\": 800, \"height\": 600}）"},
        ],
        handler_pattern=(
            'from PIL import Image, ImageDraw, ImageFont\n'
            'img = Image.open(args["input"])\n'
            'op = args["operation"]\n'
            'if op == "thumbnail":\n'
            '    img.thumbnail((args["params"]["width"], args["params"]["height"]))\n'
            'elif op == "resize":\n'
            '    img = img.resize((args["params"]["width"], args["params"]["height"]))\n'
            'elif op == "crop":\n'
            '    p = args["params"]\n'
            '    img = img.crop((p["left"], p["top"], p["right"], p["bottom"]))\n'
            'img.save(args["output"])\n'
            'return f"已生成 {args["output"]}"'
        ),
        scene_tags=["image", "resize", "crop", "filter", "watermark"],
        weak_scene_tags=["视频处理"],
    ),
    KnownGoodEntry(
        keywords=["excel", "xlsx", "xls", "电子表格", "报表", "openpyxl"],
        source_type=SourceType.PIP_PACKAGE,
        name="openpyxl",
        description="Excel 文件读写库，支持 .xlsx 格式的创建、修改和读取",
        install_hint="pip install openpyxl",
        params_template=[
            {"name": "action", "type": "string", "description": "操作：read/write"},
            {"name": "file", "type": "string", "description": "Excel 文件路径"},
            {"name": "data", "type": "object", "description": "写入数据（write 时需要）"},
        ],
        handler_pattern=(
            'from openpyxl import Workbook, load_workbook\n'
            'action = args["action"]\n'
            'if action == "read":\n'
            '    wb = load_workbook(args["file"], read_only=True)\n'
            '    ws = wb.active\n'
            '    rows = []\n'
            '    for row in ws.iter_rows(values_only=True):\n'
            '        rows.append("\\t".join(str(c or "") for c in row))\n'
            '    return "\\n".join(rows[:100])\n'
            'else:\n'
            '    wb = Workbook()\n'
            '    ws = wb.active\n'
            '    for row_data in args["data"]:\n'
            '        ws.append(row_data)\n'
            '    wb.save(args["file"])\n'
            '    return f"已保存 {args["file"]}"'
        ),
        scene_tags=["excel", "spreadsheet", "xlsx"],
        weak_scene_tags=[],
    ),
    KnownGoodEntry(
        keywords=["数据", "分析", "csv", "pandas", "表格", "统计"],
        source_type=SourceType.PIP_PACKAGE,
        name="pandas",
        description="数据分析库，支持 CSV/Excel 读取、过滤、统计、可视化数据",
        install_hint="pip install pandas",
        params_template=[
            {"name": "action", "type": "string", "description": "操作：read/stats/filter"},
            {"name": "file", "type": "string", "description": "数据文件路径"},
            {"name": "params", "type": "object", "description": "操作参数"},
        ],
        handler_pattern=(
            'import pandas as pd\n'
            'action = args["action"]\n'
            'path = args["file"]\n'
            'df = pd.read_csv(path) if path.endswith(".csv") else pd.read_excel(path)\n'
            'if action == "stats":\n'
            '    return df.describe().to_string()\n'
            'elif action == "filter":\n'
            '    col = args["params"]["column"]\n'
            '    val = args["params"]["value"]\n'
            '    result = df[df[col].astype(str).str.contains(val, na=False)]\n'
            '    return result.to_string()\n'
            'return df.head(20).to_string()'
        ),
        scene_tags=["data", "analysis", "csv", "statistics"],
        weak_scene_tags=["大数据量", "实时处理"],
    ),
    KnownGoodEntry(
        keywords=["pdf", "表格", "提取", "camelot", "pdf表格"],
        source_type=SourceType.PIP_PACKAGE,
        name="camelot-py",
        description="PDF 表格提取库，从 PDF 中精确提取表格数据",
        install_hint="pip install camelot-py[cv]",
        params_template=[
            {"name": "file", "type": "string", "description": "PDF 文件路径"},
            {"name": "page", "type": "string", "description": "页码（如 1 或 1,2,3 或 all）"},
        ],
        handler_pattern=(
            'import camelot\n'
            'page = args.get("page", "all")\n'
            'tables = camelot.read_pdf(args["file"], pages=page)\n'
            'if len(tables) == 0:\n'
            '    return "未找到表格"\n'
            'results = []\n'
            'for i, t in enumerate(tables):\n'
            '    results.append(f"--- 表格 {i+1} ---\\n{t.df.to_string()}")\n'
            'return "\\n".join(results)'
        ),
        scene_tags=["pdf", "table", "extraction"],
        weak_scene_tags=["扫描件PDF"],  # 扫描件需要 OCR
    ),
]


# ── 统一索引 ──

KNOWN_GOOD_INDEX: list[KnownGoodEntry] = CLI_TOOLS + PIP_PACKAGES
