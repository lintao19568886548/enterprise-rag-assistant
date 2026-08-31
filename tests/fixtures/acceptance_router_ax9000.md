# Acme AX9000 企业路由器验收手册

Acme AX9000 是面向分支机构的企业路由器。设备管理地址为 `192.0.2.1`，默认仅允许 HTTPS 管理。

## 状态检查

执行 `show system health`。当输出中 `Overall` 为 `Healthy` 时，表示设备自检通过。

## 恢复建议

如果状态为 `Degraded`，先保存日志，再联系管理员，不要直接断电。
