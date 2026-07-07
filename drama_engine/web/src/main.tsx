import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import "./theme/theme.css";
import { setClient } from "./api/client";
import { MockAdapter } from "./api/mockAdapter";
import { V1Adapter } from "./api/v1Adapter";
import { CreatePage } from "./pages/CreatePage";
import { HostPage } from "./pages/HostPage";
import { PlayerPage } from "./pages/PlayerPage";
import { ViewerPage } from "./pages/ViewerPage";

// 按 VITE_API_MODE 选择数据源：默认 mock（端点未就绪期），设为 v1 切真实后端。
const apiMode = (import.meta.env.VITE_API_MODE as string) || "mock";
setClient(apiMode === "v1" ? new V1Adapter() : new MockAdapter());

// 路由对齐现有后端页面路径，便于渐进替换旧 html。
const router = createBrowserRouter([
  { path: "/", element: <CreatePage /> },
  { path: "/create", element: <CreatePage /> },
  { path: "/host/sessions/:sessionId", element: <HostPage /> },
  { path: "/player", element: <PlayerPage /> },
  { path: "/viewer/sessions/:sessionId", element: <ViewerPage /> },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
