"""Haneka Team 桌面客户端 — WKWebView 壳，指向 Host168 服务端 webclient（同源，免 CORS）。"""
import os
import webview

URL = os.environ.get("HETU_TEAM_URL", "http://10.66.10.2:8877/")

def main():
    win = webview.create_window(
        title="Haneka Team",
        url=URL,
        width=1120,
        height=760,
        min_size=(900, 600),
        background_color="#f5f6f7",
    )
    webview.start()

if __name__ == "__main__":
    main()
