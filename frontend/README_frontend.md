# CampusQA 前端

CampusQA 是一个面向校园知识库问答的 Vue 3 前端。界面采用产品型工作台布局：桌面端常驻历史侧栏，中间为可滚动对话区，底部输入区固定在当前视口内；移动端历史侧栏切换为抽屉。

## 技术栈

| 类别 | 选型 | 说明 |
|------|------|------|
| 框架 | Vue 3 | Composition API 与 `<script setup>` |
| 构建 | Vite 8 | 本地开发与生产构建 |
| 样式 | TailwindCSS 4 + 自定义 CSS tokens | OKLCH 设计变量、响应式壳布局、滚动容器约束 |
| Markdown | marked.js | AI 回复 Markdown 渲染 |
| 安全 | DOMPurify | 清洗 Markdown HTML 输出 |
| 后端 | FastAPI 代理 | `/api/*` 转发到 `localhost:8000` |

## 快速开始

```bash
cd frontend
npm install
npm run dev
```

默认访问地址：

```text
http://localhost:5173
```

需要同时启动后端服务，Vite 会把 `/api/*` 代理到 `http://localhost:8000`。

## 项目结构

```text
frontend/
├── DESIGN.md                       # 当前视觉系统说明
├── PRODUCT.md                      # 产品语境与设计原则
├── README_frontend.md
├── index.html
├── package.json
├── vite.config.js                  # Tailwind 插件与 API 代理
├── public/
│   ├── favicon.svg
│   └── icons.svg
└── src/
    ├── main.js
    ├── sessionList.js              # 历史会话列表本地合并/更新
    ├── style.css                   # OKLCH tokens、布局、Markdown、滚动规则
    ├── App.vue                     # 应用壳、状态管理、API 调用
    └── components/
        ├── ChatBubble.vue          # 用户/助手消息、复制、编辑、删除、重新生成
        ├── ChatInput.vue           # 固定输入区、上传、清空、发送
        ├── ChatMessages.vue        # 独立滚动消息区与空状态
        ├── ChatSidebar.vue         # 响应式历史侧栏与历史列表滚动
        └── ErrorToast.vue          # 成功/错误反馈提示
```

## 界面布局

- **桌面端**：`.app-shell` 使用两列布局，左侧历史侧栏宽度为 `clamp(17rem, 19vw, 21rem)`，右侧为会话工作台。
- **移动端**：`900px` 以下历史侧栏变为抽屉，默认收起，点击顶部菜单按钮展开。
- **会话面板**：主面板是严格的纵向 flex 结构，顶部 header 固定，中间 `.chat-scroll` 独立滚动，底部 `ChatInput` 使用 `shrink-0` 固定在视口内。
- **历史侧栏**：`.sidebar-shell` 固定到 `100dvh`，`.sidebar-scroll` 独立滚动，历史记录较多时不会撑开页面。
- **视觉系统**：`src/style.css` 使用 OKLCH tokens，支持系统深色模式和 `prefers-reduced-motion`。

## 组件说明

### App.vue

- 管理消息、会话、加载态、错误/成功提示、Agent/RAG 模式与知识库操作。
- 调用接口：`/api/chat`、`/api/sessions`、`/api/session/*`、`/api/mode`、`/api/upload`、`/api/kb/*`。
- 收到 `/api/chat` 返回的新 `session_id` 后，会立即将该会话合并到历史侧栏，无需刷新页面。
- 保持会话工作台的三段式布局：header、消息滚动区、输入区。

### sessionList.js

- 提供 `upsertSessionFromChatResponse()`，把聊天响应中的 `session_id`、`session_title` 合并到本地历史列表。
- 新会话插入列表顶部；已有会话更新标题、消息数和更新时间，并避免重复项。

### ChatSidebar.vue

- **Props**：`visible`、`sessions`、`currentSessionId`
- **Emits**：`close`、`select`、`new`、`delete`
- 桌面端常驻显示，移动端作为抽屉显示。
- 历史列表使用独立滚动容器，支持较多会话上下滚动。

### ChatMessages.vue

- **Props**：`messages`、`isLoading`
- **Emits**：`copy`、`delete`、`edit`、`reroll`
- 消息列表区域使用 `.chat-scroll`，长对话时只滚动消息区，不会把输入框推到屏幕外。
- 监听消息数量和加载态变化，自动滚动到底部。

### ChatBubble.vue

- **Props**：`role`、`content`、`isLoading`、`index`、`isLastAi`
- 用户消息右对齐；助手消息使用文档式卡片排版。
- 支持复制、删除、编辑用户消息并重新生成，以及重新生成最后一条助手回复。
- Markdown 内容经过 `marked.parse()` 和 `DOMPurify.sanitize()` 后渲染。

### ChatInput.vue

- **Props**：`disabled`
- **Emits**：`send(text)`、`clear`、`upload(file)`
- 输入区固定在会话面板底部，不随长对话内容移动到屏幕外。
- `<textarea>` 自动增高，最大高度为 `10.5rem`。
- Enter 发送，Shift + Enter 换行。
- 支持 `.md`、`.txt`、`.pdf`、`.html` 文件上传。

### ErrorToast.vue

- **Props**：`message`、`visible`、`type`
- `type="success"` 显示成功反馈，默认错误反馈。
- 自动 5 秒消失，也支持手动关闭。

## API 代理

`vite.config.js` 中配置：

```js
server: {
  proxy: {
    '/api': 'http://localhost:8000',
  },
}
```

前端请求 `/api/*` 时会自动转发到 FastAPI 后端。

## 会话持久化

- 当前 `sessionId` 存储在 `localStorage` 的 `campusqa_session_id`。
- 页面刷新后会尝试通过 `/api/session/{sessionId}` 恢复历史。
- 首轮发送创建新会话时，前端会立即更新历史侧栏；刷新页面不再是看到新会话的前提。
- 新建或清空会话时会清除本地 `sessionId`。

## 验证

常规构建验证：

```bash
npm run build
```

当前布局已使用以下视口做过浏览器验证：

- `1920x1080`：桌面侧栏常驻，历史列表独立滚动。
- `1366x768`：真实历史会话下消息区可滚动，输入区保持在视口内。
- `1024x640`：较矮桌面视口下输入区不被长对话推出屏幕。
- `768x600`：窄屏/平板宽度下主对话区无横向溢出。
- `390x844`：移动端抽屉侧栏可展开，消息区可滚动，输入区保持可见。

## 改动日志

### v1.1.2 (2026-06-13)

- 修复新会话创建后不会立即出现在历史侧栏的问题。
- 新增 `sessionList.js`，集中处理聊天响应到历史列表的本地合并逻辑。
- 前端 package 版本升至 `1.1.2`。

### v1.1.0

- 重写为产品型 CampusQA 工作台界面。
- 增加桌面常驻历史侧栏与移动端抽屉侧栏。
- 增加 `.sidebar-scroll` 和 `.chat-scroll` 独立滚动容器。
- 修复长对话把输入框推到屏幕外的问题。
- 修复历史会话较多时无法独立上下滚动的问题。
- 增加成功/错误两种 toast 反馈样式。
- 新增 `PRODUCT.md`、`DESIGN.md` 与 `.impeccable/live/config.json`。

### v1.0.0 (2026-06-10)

- 初始聊天界面。
- ChatBubble 消息气泡与 Markdown 渲染。
- ChatMessages 自动滚动到底部。
- ChatInput auto-resize、Enter 发送、Shift + Enter 换行。
- ErrorToast 网络错误和超时提示。
- localStorage 会话持久化。
- Vite 代理 `/api` 到 FastAPI 后端。
