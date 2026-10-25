/** 首次配置向导（§1.5 / §10.2）：隐私告知 → 密钥 → 自检确认。 */
import { useEffect, useState } from "react";
import { Alert, Button, Checkbox, Descriptions, Form, Input, Modal, Result, Space, Spin, Steps, Typography } from "antd";
import { get, post, put } from "../api/client";
import type { Selfcheck } from "../api/types";
import { ErrorPanel } from "../components/ErrorBoundary";
import { useApp } from "../store";

export function Wizard({ open, onFinish }: { open: boolean; onFinish: () => void }) {
  const [step, setStep] = useState(0);
  const [agree, setAgree] = useState(false);
  const [form] = Form.useForm();
  const [testing, setTesting] = useState<"deepseek" | "qwen" | null>(null);
  const [testMsg, setTestMsg] = useState<{ provider: string; ok: boolean; msg: string } | null>(null);
  const [selfcheck, setSelfcheck] = useState<Selfcheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<null | { userMessage: string; code: string; traceId: string }>(null);
  const { refreshSettings } = useApp();

  useEffect(() => {
    if (open && step === 2 && !selfcheck) {
      setChecking(true);
      void post<Selfcheck>("/api/selfcheck").then(setSelfcheck).catch(() => setSelfcheck(null)).finally(() => setChecking(false));
    }
  }, [open, step, selfcheck]);

  useEffect(() => {
    if (open) { setStep(0); setAgree(false); setError(null); }
  }, [open]);

  const testKey = async (provider: "deepseek" | "qwen") => {
    const v = form.getFieldsValue();
    setTesting(provider);
    setTestMsg(null);
    try {
      const body = provider === "deepseek"
        ? { provider, api_key: v.ds_key }
        : { provider, api_key: v.qw_key };
      const res = await post<{ ok: boolean; message: string }>("/api/settings/test-connection", body);
      setTestMsg({ provider, ok: true, msg: res.message });
    } catch (e) {
      const err = (e as { error?: { userMessage: string } }).error;
      setTestMsg({ provider, ok: false, msg: err?.userMessage || "连接失败" });
    } finally {
      setTesting(null);
    }
  };

  const saveAndFinish = async () => {
    try {
      const v = form.getFieldsValue();
      await put("/api/settings", {
        deepseek: { api_key: v.ds_key || "" },
        qwen: { api_key: v.qw_key || "" },
      });
      await post("/api/settings/complete-setup", { privacy_consent: agree });
      setError(null);
      await refreshSettings();
      onFinish();
    } catch (e) {
      const err = (e as { error?: { userMessage: string; code: string; traceId: string } }).error;
      setError(err || { userMessage: "保存失败", code: "", traceId: "" });
    }
  };

  const skip = async () => {
    // 暂不配置：仅标记向导完成，之后可从「设置 → 模型与 API Key」重新进入
    await post("/api/settings/complete-setup", { privacy_consent: agree });
    await refreshSettings();
    onFinish();
  };

  return (
    <Modal open={open} closable={false} maskClosable={false} footer={null} width={640}
      title="首次配置向导">
      <Steps current={step} size="small" style={{ marginBottom: 24 }}
        items={[{ title: "欢迎与隐私说明" }, { title: "模型服务密钥" }, { title: "环境自检" }]} />
      {step === 0 && (
        <div>
          <Typography.Title level={5}>欢迎使用论文写作助手</Typography.Title>
          <Typography.Paragraph>
            本工具把「资料入库 → 章节设计 → 大纲设计 → 逐章初稿 → 全文导出」串成一条可回溯、可修改的写作工作流。
            所有文档、数据库与向量索引都保存在本机 <Typography.Text code>data/</Typography.Text> 目录。
          </Typography.Paragraph>
          <Alert
            type="info"
            showIcon
            message="隐私提示"
            description={
              <div>
                <p>1. 文档内容会发送给 DeepSeek 与阿里云百炼（Qwen）用于解析、摘要、生成与向量化；</p>
                <p>2. API Key 只保存在本机 data/config.json，由你自己的账户承担调用费用；</p>
                <p>3. 本产品是写作辅助工具，不提供代写服务，最终文本、观点与引注由作者负责，请遵守所在学校的学术规范。</p>
              </div>
            }
          />
          <Checkbox checked={agree} onChange={(e) => setAgree(e.target.checked)} style={{ marginTop: 16 }}>
            我已阅读并同意上述说明
          </Checkbox>
          <div style={{ marginTop: 24, textAlign: "right" }}>
            <Space>
              <Button type="link" onClick={() => void skip()}>暂不配置，直接使用</Button>
              <Button type="primary" disabled={!agree} onClick={() => setStep(1)}>下一步</Button>
            </Space>
          </div>
        </div>
      )}
      {/* 表单保持挂载（display:none 隐藏）：切步骤卸载会丢 antd 表单仓库里的值 */}
      <div style={{ display: step === 1 ? "block" : "none" }}>
        <Form form={form} layout="vertical">
          <Typography.Paragraph type="secondary">
            分别填写两个服务的密钥（可只填一个：缺 DeepSeek 时生成类功能不可用；缺 Qwen 时语义检索不可用，关键词检索仍可用）。
            密钥只保存在本机，不会上传到任何开发者服务器。
          </Typography.Paragraph>
          <Form.Item label="DeepSeek 模型服务密钥（API Key）" name="ds_key">
            <Input.Password placeholder="sk-…（在 platform.deepseek.com 获取）" autoComplete="off" />
          </Form.Item>
          <Space>
            <Button size="small" loading={testing === "deepseek"} onClick={() => testKey("deepseek")}>测试连接</Button>
            {testMsg?.provider === "deepseek" && (
              <Typography.Text type={testMsg.ok ? "success" : "danger"}>{testMsg.msg}</Typography.Text>
            )}
          </Space>
          <Form.Item label="阿里云百炼 / Qwen 模型服务密钥（API Key）" name="qw_key" style={{ marginTop: 16 }}>
            <Input.Password placeholder="sk-…（在百炼控制台获取）" autoComplete="off" />
          </Form.Item>
          <Space>
            <Button size="small" loading={testing === "qwen"} onClick={() => testKey("qwen")}>测试连接</Button>
            {testMsg?.provider === "qwen" && (
              <Typography.Text type={testMsg.ok ? "success" : "danger"}>{testMsg.msg}</Typography.Text>
            )}
          </Space>
          <div style={{ marginTop: 24, textAlign: "right" }}>
            <Space>
              <Button onClick={() => setStep(0)}>上一步</Button>
              <Button type="primary" onClick={() => setStep(2)}>下一步</Button>
            </Space>
          </div>
        </Form>
      </div>
      {step === 2 && (
        <div>
          <Typography.Title level={5}>环境自检</Typography.Title>
          {checking ? <Spin /> : selfcheck ? (
            <Descriptions size="small" column={1} bordered>
              {selfcheck.items.map((it) => (
                <Descriptions.Item key={it.name} label={it.name}>
                  <Typography.Text type={it.ok ? "success" : "danger"}>
                    {it.ok ? "✓ 正常" : "✗ 异常"} {it.detail}
                  </Typography.Text>
                </Descriptions.Item>
              ))}
            </Descriptions>
          ) : (
            <Alert type="warning" showIcon message="无法完成自检，请确认后端正在运行" />
          )}
          {error && (
            <div style={{ marginTop: 12 }}>
              <ErrorPanel error={{ ...error, stage: "config", retryable: false, message: "", suggestion: undefined, detail: undefined } as never} />
            </div>
          )}
          <div style={{ marginTop: 24, textAlign: "right" }}>
            <Space>
              <Button onClick={() => setStep(1)}>上一步</Button>
              <Button type="primary" onClick={saveAndFinish}>完成，开始使用</Button>
            </Space>
          </div>
        </div>
      )}
    </Modal>
  );
}
