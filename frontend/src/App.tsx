/** 应用外壳（§3.1 顶部导航 / §3.4 能力状态横幅）。 */
import { useEffect, useState } from "react";
import { HashRouter, Link, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { Alert, Button, Dropdown, Layout, Space } from "antd";
import { FileTextOutlined, ApartmentOutlined, SettingOutlined, KeyOutlined, DownOutlined } from "@ant-design/icons";
import { useApp } from "./store";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { SettingsDrawer } from "./pages/SettingsDrawer";
import { Wizard } from "./pages/Wizard";
import { FilesPage } from "./pages/FilesPage";
import { DesignsPage } from "./pages/DesignsPage";
import { DesignEditor } from "./pages/DesignEditor";
import { OutlinesPage, AllOutlinesPage } from "./pages/OutlinesPage";
import { OutlineEditor } from "./pages/OutlineEditor";
import { DraftsPage, AllDraftsPage } from "./pages/DraftsPage";
import { DraftEditor } from "./pages/DraftEditor";
import { ExperimentPlansPage } from "./pages/ExperimentPlans";
import { ExperimentPlanEditor } from "./pages/ExperimentPlanEditor";

function NavShell() {
  const location = useLocation();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const { settings } = useApp();

  const keysMissing = settings && (settings.key_status.deepseek === "missing" || settings.key_status.qwen === "missing");

  // 章节设计模块包含三个子页面（§3.2 页面地图）：章节生成 / 大纲设计 / 章节创作
  const isDesignModule =
    location.pathname.startsWith("/designs") ||
    location.pathname.startsWith("/outlines") ||
    location.pathname.startsWith("/drafts") ||
    location.pathname.startsWith("/plans");
  const isFiles = location.pathname.startsWith("/files");
  const navigate = useNavigate();

  return (
    <Layout className="app-layout">
      <Layout.Header className="app-header">
        <div className="app-title">论文写作助手</div>
        <nav className="app-nav">
          <Link to="/files"
            className={`app-nav-item ${isFiles ? "active" : ""}`}>
            <FileTextOutlined /> 文件入库
          </Link>
          <Dropdown
            menu={{
              items: [
                { key: "/designs", label: "章节设计（整体思路与章节树）" },
                { key: "/outlines", label: "大纲设计（逐节写作方案）" },
                { key: "/drafts", label: "章节创作（逐章写正文）" },
                { key: "/plans", label: "实验方案设计（理工版）" },
              ],
              selectedKeys: isDesignModule ? [location.pathname.split("/")[1]] : [],
              onClick: ({ key }) => navigate(key),
            }}
          >
            <span className={`app-nav-item ${isDesignModule ? "active" : ""}`}
              style={{ cursor: "pointer" }}>
              <ApartmentOutlined /> 章节设计 <DownOutlined style={{ fontSize: 10 }} />
            </span>
          </Dropdown>
        </nav>
        <span style={{ flex: 1 }} />
        <Button type="text" icon={<SettingOutlined />} onClick={() => setSettingsOpen(true)}>
          设置
        </Button>
      </Layout.Header>
      {keysMissing && (
        <Alert
          type="warning"
          banner
          showIcon
          icon={<KeyOutlined />}
          message={
            <Space>
              尚未配置模型服务密钥，AI 功能不可用（本地文献仍可浏览与解析）
              <Button size="small" onClick={() => setSettingsOpen(true)}>去「模型与 API Key」配置</Button>
            </Space>
          }
        />
      )}
      <Layout.Content className="app-content">
        <ErrorBoundary>
          <Routes>
            <Route path="/files" element={<FilesPage />} />
            <Route path="/designs" element={<DesignsPage />} />
            <Route path="/designs/:id" element={<DesignEditor />} />
            <Route path="/outlines" element={<OutlinesPage />} />
            <Route path="/outlines/all" element={<AllOutlinesPage />} />
            <Route path="/outlines/:id" element={<OutlineEditor />} />
            <Route path="/drafts" element={<DraftsPage />} />
            <Route path="/drafts/all" element={<AllDraftsPage />} />
            <Route path="/drafts/:id" element={<DraftEditor />} />
            <Route path="/plans" element={<ExperimentPlansPage />} />
            <Route path="/plans/:id" element={<ExperimentPlanEditor />} />
            <Route path="*" element={<FilesPage />} />
          </Routes>
        </ErrorBoundary>
      </Layout.Content>
      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </Layout>
  );
}

export default function App() {
  const { settings, loadingSettings, refreshSettings } = useApp();
  const [wizardOpen, setWizardOpen] = useState(false);

  useEffect(() => {
    void refreshSettings();
  }, [refreshSettings]);

  useEffect(() => {
    if (!loadingSettings && settings && !settings.setup_done) setWizardOpen(true);
  }, [settings, loadingSettings]);

  if (loadingSettings) return <div className="app-loading">加载中…</div>;
  return (
    <>
      <Wizard open={wizardOpen} onFinish={() => { setWizardOpen(false); void refreshSettings(); }} />
      <NavShell />
    </>
  );
}
