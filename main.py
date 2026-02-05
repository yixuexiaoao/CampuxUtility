"""HTML转图片服务启动入口

这是一个基于FastAPI和Playwright的HTML转图片服务。
支持将HTML内容或Jinja2模板渲染为图片文件。

主要功能：
- HTML字符串转图片
- Jinja2模板渲染转图片
- 自定义截图选项
- 多种返回格式支持

使用方法：
    python main.py
    
环境变量：
    PORT: 服务端口号，默认为8999
"""

import os

if __name__ == "__main__":
    import uvicorn

    # 从环境变量获取端口号，默认使用8999
    port = os.getenv("PORT", 8999)

    # 启动FastAPI服务器
    uvicorn.run("src.api:app", host="0.0.0.0", port=port)
