/** 设置抽屉（§3.1 / §10.2 / §11.4）：密钥、日志与诊断、使用说明、关于。 */
import { useEffect, useState } from "react";
import { Alert, Button, Card, Descriptions, Drawer, Form, Input, Modal, Space, Tabs, Tag, Typography, message } from "antd";
import { CheckCircleOutlined, CloseCircleOutlined } from "@ant-design/icons";
import { download, get, post, put } from "../api/client";
import type { Selfcheck, Settings } from "../api/types";
import { ErrorPanel } from "../components/ErrorBoundary";
import { useApp } from "../store";

function KeyCard({ provider, title, desc }: { provider: "deepseek" | "qwen"; title: string; desc: string }) {
  const { settings, refreshSettings } = useApp();
  const [form] = Form.useForm();
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const cfg = settings?.config?.[provider];
  const status = settings?.key_status?.[provider] || "missing";

  useEffect(() => {
    if (settings) form.setFieldsValue({ api_key: "", base_url: cfg?.base_url });
  }, [settings, form, cfg?.base_url]);

  const save = async () => {
    const v = form.getFieldsValue();
    const patch: Record<string, string> = {};
    if (v.api_key && !v.api_key.startsWith("****")) patch.api_key = v.api_key.trim();
    if (v.base_url) patch.base_url = v.base_url.trim();
    await put("/api/settings", { [provider]: patch });
    await refreshSettings();
    form.setFieldValue("api_key", "");
    message.success("已保存");
  };

  const clearKey = async () => {
    await put("/api/settings", { [provider]: { api_key: "" } });
    await refreshSettings();
    message.success("已清除密钥");
  };

  const test = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const v = form.getFieldsValue();
      const res = await post<{ ok: boolean; message: string }>("/api/settings/test-connection", {
        provider, api_key: v.api_key || undefined,
        base_url: v.base_url || undefined,
      });
      setTestResult({ ok: res.ok, msg: res.message });
    } catch (e) {
      const err = (e as { error?: { userMessage: string } }).error;
      setTestResult({ ok: false, msg: err?.userMessage || "连接失败" });
    } finally {
      setTesting(false);
    }
  };

  return (
    <Card size="small" title={title} extra={
      status === "configured" ? <Tag color="success">已验证/已配置</Tag> : <Tag>未配置</Tag>
    }>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>{desc}</Typography.Paragraph>
      <Form form={form} layout="vertical" size="small">
        <Form.Item label="模型服务密钥（API Key）" name="api_key">
          <Input.Password placeholder={status === "configured" ? "已配置（末4位 " + (cfg?.api_key || "") + "），输入新密钥可替换" : "粘贴密钥"} autoComplete="off" />
        </Form.Item>
        <Form.Item label="服务地址（高级，一般不用改）" name="base_url">
          <Input placeholder={cfg?.base_url} />
        </Form.Item>
      </Form>
      <Space wrap>
        <Button size="small" type="primary" onClick={save}>保存</Button>
        <Button size="small" loading={testing} onClick={test}>测试连接</Button>
        {status === "configured" && <Button size="small" danger onClick={clearKey}>清除密钥</Button>}
        {testResult && (
          <Typography.Text type={testResult.ok ? "success" : "danger"}>
            {testResult.ok ? <CheckCircleOutlined /> : <CloseCircleOutlined />} {testResult.msg}
          </Typography.Text>
        )}
      </Space>
    </Card>
  );
}

function DiagnosticsPanel() {
  const [selfcheck, setSelfcheck] = useState<Selfcheck | null>(null);
  const [envInfo, setEnvInfo] = useState<Record<string, unknown> | null>(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    void get<Record<string, unknown>>("/api/diagnostics/info").then(setEnvInfo).catch(() => {});
  }, []);

  const runSelfcheck = async () => {
    setRunning(true);
    try { setSelfcheck(await post<Selfcheck>("/api/selfcheck")); } finally { setRunning(false); }
  };
  const copyInfo = () => {
    void navigator.clipboard.writeText(JSON.stringify(envInfo, null, 2));
    message.success("已复制环境信息");
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Card size="small" title="环境信息" extra={<Button size="small" onClick={copyInfo}>复制环境信息</Button>}>
        <Descriptions size="small" column={2}>
          <Descriptions.Item label="应用版本">{String(envInfo?.app_version || "-")}</Descriptions.Item>
          <Descriptions.Item label="Python">{String(envInfo?.python_version || "-")}</Descriptions.Item>
          <Descriptions.Item label="系统">{String(envInfo?.os || "-")} / {String(envInfo?.arch || "-")}</Descriptions.Item>
          <Descriptions.Item label="端口">{String(envInfo?.port || "-")}</Descriptions.Item>
          <Descriptions.Item label="数据目录" span={2}>{String(envInfo?.data_dir || "-")}</Descriptions.Item>
          <Descriptions.Item label="数据库大小" span={2}>{envInfo?.db_size != null ? (Number(envInfo.db_size) / 1048576).toFixed(1) + " MB" : "-"}</Descriptions.Item>
          <Descriptions.Item label="密钥状态" span={2}>
            DeepSeek：{String((envInfo?.key_status as Record<string, string>)?.deepseek || "-")} ｜
            Qwen：{String((envInfo?.key_status as Record<string, string>)?.qwen || "-")}
          </Descriptions.Item>
        </Descriptions>
      </Card>
      <Card size="small" title="操作">
        <Space wrap>
          <Button onClick={() => void post("/api/diagnostics/open-logs")}>打开日志目录</Button>
          <Button onClick={() => void download("/api/diagnostics/export")}>导出诊断包</Button>
          <Button loading={running} onClick={runSelfcheck}>运行自检</Button>
          <Button danger onClick={() => {
            if (window.confirm("确定清理全部日志？")) void post("/api/diagnostics/clear-logs").then(() => message.success("已清理"));
          }}>清理日志</Button>
        </Space>
        {selfcheck && (
          <div style={{ marginTop: 12 }}>
            <Alert type={selfcheck.passed ? "success" : "warning"} showIcon
              message={`自检${selfcheck.passed ? "通过" : "存在异常"}`}
              description={selfcheck.items.filter((i) => !i.ok).map((i) => `${i.name}: ${i.detail}`).join("；") || undefined} />
          </div>
        )}
      </Card>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        自助排查顺序：记下界面上的错误码与诊断号 → 打开日志目录，在当日日志中搜索诊断号 →
        查看 error-*.log 中的错误详情 → 运行自检；仍无法解决时导出诊断包，连同诊断号一起反馈。
      </Typography.Paragraph>
    </Space>
  );
}

export function SettingsDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Drawer open={open} onClose={onClose} width={720} title="设置">
      <Tabs
        items={[
          {
            key: "keys",
            label: "模型与 API Key",
            children: (
              <Space direction="vertical" style={{ width: "100%" }} size="middle">
                <Alert type="info" showIcon
                  message="费用说明：调用费用由你自己的 DeepSeek / 百炼账户承担，产品不经手。密钥只保存在本机 data/config.json。获取入口：platform.deepseek.com/api_keys、bailian.console.aliyun.com" />
                <KeyCard provider="deepseek" title="DeepSeek（生成模型）"
                  desc="章节设计、大纲、正文、摘要与元数据抽取" />
                <KeyCard provider="qwen" title="阿里云百炼 / Qwen（嵌入模型）"
                  desc="文本向量化与语义检索；未配置时仅关键词检索可用" />
              </Space>
            ),
          },
          { key: "diag", label: "日志与诊断", children: <DiagnosticsPanel /> },
          {
            key: "help",
            label: "使用说明",
            children: (
              <div className="markdown-view">
                <h3>工作流</h3>
                <ol>
                  <li><b>文件入库</b>：上传 .docx / .doc / .pdf 文献，自动解析、分块、建立索引；</li>
                  <li><b>章节设计</b>：填写写作意图与目标字数，AI 检索文献并生成章节树与字数预算，确认前做冲突检查；</li>
                  <li><b>大纲设计</b>：对每个叶子章节生成简化写作方案（写什么、怎么论证、用什么材料）；</li>
                  <li><b>章节创作</b>：逐章生成正文，引用自动标记 <code>[章节号-序号]</code>，证据不足处用提示块标注；</li>
                  <li><b>导出</b>：工作稿（含全部追溯信息）或定稿（正文 + GB/T 7714 参考文献）。</li>
                </ol>
                <h3>常见问题</h3>
                <ul>
                  <li>生成结果引用标红：模型生成的引用无法对应到检索片段，可在引用列表中重新指定来源片段；</li>
                  <li>提示块 &lt;NOTICE&gt;：需要作者核实的法条、案例、数据，导出定稿时默认移除；</li>
                  <li>引用格式：定稿默认 GB/T 7714-2015 顺序编码制，可在导出时切换章节编码制；</li>
                  <li>备份：直接复制整个项目文件夹即可，全部数据在 data/ 目录。</li>
                </ul>
              </div>
            ),
          },
          {
            key: "about",
            label: "关于",
            children: (
              <Space direction="vertical" style={{ width: "100%" }}>
                <Typography.Title level={5}>文科论文写作助手 v0.5.1</Typography.Title>
                <Typography.Paragraph>
                  面向文科（以法学为主）学生的论文辅助写作工具。本地运行，数据不出本机
                  （模型调用除外）。本产品不提供代写服务，不承诺规避查重或 AIGC 检测，
                  请遵守所在学校的学术规范。
                </Typography.Paragraph>
                <Typography.Title level={5} type="danger">危险操作</Typography.Title>
                <Alert
                  type="error"
                  showIcon
                  message="清空全部数据"
                  description={
                    <div>
                      <p>删除全部文献、片段、章节设计、大纲、创作稿、任务记录、原文件、
                      向量索引、导出文件与日志，不可恢复。</p>
                      <p>模型服务密钥（API Key）保留。</p>
                      <Button danger onClick={() => {
                        Modal.confirm({
                          title: "确定清空全部数据？",
                          content: "所有文献与论文内容将被彻底删除，不可恢复。建议先备份 data 目录。",
                          okText: "确认清空", okButtonProps: { danger: true },
                          onOk: async () => {
                            try {
                              await post("/api/clear-data");
                              message.success("已清空全部数据");
                            } catch (e) {
                              message.error((e as { error?: { userMessage: string } }).error?.userMessage || "清空失败");
                            }
                          },
                        });
                      }}>
                        清空全部数据
                      </Button>
                    </div>
                  }
                />
              </Space>
            ),
          },
        ]}
      />
    </Drawer>
  );
}
