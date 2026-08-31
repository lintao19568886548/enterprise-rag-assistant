from app.import_process.agent.nodes.node_document_split import infer_page_number


def test_page_inference_uses_matching_mineru_text():
    entries = [
        (4, "配置静态路由"),
        (6, "在无线控制器上执行 display wlan ap 命令"),
    ]

    assert infer_page_number(
        "## 验证连接\n在无线控制器上执行 display wlan ap 命令，state 显示 R/M。",
        entries,
    ) == 6


def test_page_inference_returns_none_without_evidence():
    assert infer_page_number("完全无关的内容", [(2, "配置 DHCP 地址池")]) is None
