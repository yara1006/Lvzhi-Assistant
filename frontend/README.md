# 律智助手 · 前端

> 基于腾讯元器智能体 API 的法律 AI 前端项目

---

## 文件结构

```
frontend/
├── README.md           # 项目说明书
├── index.html          # 前端主页面
├── login.html          # 登录页面（手机号 + 验证码登录）
├── config.js           # 前端配置
├── server.js           # 后端代理（隐藏 Token，必须运行）
├── css/
│   ── style.css       # 页面样式
└── js/
    ├── main.js         # 页面启动入口
    ├── chat.js         # 聊天核心逻辑
    ├── tools.js        # 工具切换和 API 请求
    └── utils.js        # 通用工具函数
```

---

## 配置

编辑 `config.js`：

```javascript
window.YUANQI_CONFIG = {
  DEMO_MODE: false,   // 改为 true 启用 Demo 模式
};
```

---

## 启动

1. 启动后端 API（见项目根目录 README）
2. 用浏览器打开 `login.html` 登录
3. 登录成功后跳转到 `index.html`

页面加载顺序：`config.js` → `utils.js` → `tools.js` → `chat.js` → `main.js`

---

## Demo 模式

如果 `DEMO_MODE: true`，会使用内置模拟数据演示功能，不调用真实 API。
