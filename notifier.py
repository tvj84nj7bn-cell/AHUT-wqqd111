# -*- coding: utf-8 -*-
"""
签到结果通知模块
支持：邮件通知（QQ邮箱/163邮箱等）、Server酱微信推送
"""
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formataddr, make_msgid
import urllib.request
import urllib.parse
import logging

logger = logging.getLogger(__name__)


class Notifier:
    """签到结果通知器"""

    def __init__(self, config=None):
        """
        初始化通知器
        config 示例：
        {
            "email": {
                "enabled": true,
                "smtp_server": "smtp.qq.com",
                "smtp_port": 465,
                "sender": "xxx@qq.com",
                "auth_code": "邮箱授权码",
                "receiver": "xxx@qq.com"
            },
            "serverchan": {
                "enabled": false,
                "sendkey": "SCTxxxxxxxxxxx"
            }
        }
        """
        self.config = config or {}
        self.email_cfg = self.config.get("email", {})
        self.scfgs = self.config.get("serverchan", {})

    def send(self, title, content):
        """发送通知（所有已启用的渠道）"""
        results = []

        if self.email_cfg.get("enabled"):
            try:
                ok = self._send_email(title, content)
                results.append(("邮件", ok))
            except Exception as e:
                logger.error(f"邮件发送异常: {e}")
                results.append(("邮件", False))

        if self.scfgs.get("enabled"):
            try:
                ok = self._send_serverchan(title, content)
                results.append(("Server酱", ok))
            except Exception as e:
                logger.error(f"Server酱发送异常: {e}")
                results.append(("Server酱", False))

        return results

    # ---------- 邮件通知 ----------

    def _send_email(self, title, content):
        """通过SMTP发送邮件，支持多个收件人（用分号;或逗号,分隔）"""
        smtp_server = self.email_cfg.get("smtp_server", "smtp.qq.com")
        smtp_port = int(self.email_cfg.get("smtp_port", 465))
        sender = self.email_cfg.get("sender", "")
        auth_code = self.email_cfg.get("auth_code", "")
        receiver_raw = self.email_cfg.get("receiver", "")

        # 解析多个收件人，支持分号;和逗号,分隔
        receivers = [r.strip() for r in receiver_raw.replace(',', ';').split(';') if r.strip()]

        if not all([sender, auth_code]) or not receivers:
            logger.warning("邮件配置不完整（缺少发件人/授权码/收件人），跳过邮件发送")
            return False

        msg = MIMEMultipart()
        # 标准格式的发件人：显示名称 <邮箱地址>
        msg['From'] = formataddr((str(Header("签到助手", 'utf-8')), sender))
        # 多个收件人用逗号分隔
        msg['To'] = ", ".join(receivers)
        msg['Subject'] = Header(title, 'utf-8')
        # 添加Message-ID，避免被当成垃圾邮件
        msg['Message-ID'] = make_msgid(domain=sender.split('@')[-1] if '@' in sender else 'localhost')

        # HTML格式邮件，更好看
        html_content = f"""
        <div style="font-family: Microsoft YaHei, Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px; border-radius: 10px 10px 0 0;">
                <h2 style="color: white; margin: 0; text-align: center;">晚寝签到结果</h2>
            </div>
            <div style="background: #f8f9fa; padding: 25px; border-radius: 0 0 10px 10px; border: 1px solid #e9ecef;">
                {content}
            </div>
            <div style="text-align: center; color: #999; font-size: 12px; margin-top: 15px;">
                本邮件由 AHUT 签到助手自动发送
            </div>
        </div>
        """
        msg.attach(MIMEText(html_content, 'html', 'utf-8'))

        try:
            if smtp_port == 465:
                server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=15)
            else:
                server = smtplib.SMTP(smtp_server, smtp_port, timeout=15)
                server.starttls()
            server.login(sender, auth_code)
            server.sendmail(sender, receivers, msg.as_string())
            server.quit()
            logger.info(f"邮件发送成功 -> {len(receivers)}个收件人: {', '.join(receivers)}")
            return True
        except Exception as e:
            logger.error(f"邮件发送失败: {e}")
            return False

    # ---------- Server酱微信推送 ----------

    def _send_serverchan(self, title, content):
        """通过Server酱发送微信推送"""
        sendkey = self.scfgs.get("sendkey", "")
        if not sendkey:
            logger.warning("Server酱 SendKey 未配置，跳过")
            return False

        # Server酱 Turbo版 API
        url = f"https://sctapi.ftqq.com/{sendkey}.send"
        data = urllib.parse.urlencode({
            "title": title,
            "desp": content
        }).encode('utf-8')

        try:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode('utf-8'))
            if result.get("code") == 0:
                logger.info("Server酱推送成功")
                return True
            else:
                logger.error(f"Server酱推送失败: {result.get('message', '未知错误')}")
                return False
        except Exception as e:
            logger.error(f"Server酱推送异常: {e}")
            return False


def build_sign_result_content(results, users, elapsed):
    """
    构建签到结果的HTML内容
    results: [{'success': bool, 'data': set}, ...]
    users: [User, ...]
    elapsed: 耗时秒数
    """
    success_count = sum(1 for r in results if r['success'])
    total = len(results)
    all_success = success_count == total

    if all_success:
        status_badge = '<span style="background: #28a745; color: white; padding: 4px 12px; border-radius: 12px; font-size: 14px;">全部成功</span>'
    else:
        status_badge = f'<span style="background: #dc3545; color: white; padding: 4px 12px; border-radius: 12px; font-size: 14px;">部分失败 ({success_count}/{total})</span>'

    rows_html = ""
    for user, result in zip(users, results):
        if result['success']:
            status_html = '<td style="color: #28a745; font-weight: bold;">✓ 成功</td>'
        else:
            errors = "、".join(result['data']) if result['data'] else "未知错误"
            status_html = f'<td style="color: #dc3545; font-weight: bold;">✗ 失败<br><span style="font-size: 12px; font-weight: normal;">{errors}</span></td>'

        rows_html += f"""
        <tr style="background: white;">
            <td style="padding: 10px; border-bottom: 1px solid #eee; font-family: Consolas, monospace;">{user.student_Id}</td>
            <td style="padding: 10px; border-bottom: 1px solid #eee;">{user.username or '未知'}</td>
            {status_html}
        </tr>
        """

    from datetime import datetime
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = f"""
    <div style="text-align: center; margin-bottom: 20px;">
        {status_badge}
        <p style="color: #666; margin-top: 10px; font-size: 14px;">
            共 {total} 人，成功 {success_count} 人，失败 {total - success_count} 人，耗时 {elapsed:.1f}秒
        </p>
        <p style="color: #999; font-size: 12px;">{now_str}</p>
    </div>
    <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
        <thead>
            <tr style="background: #e9ecef;">
                <th style="padding: 10px; text-align: left;">学号</th>
                <th style="padding: 10px; text-align: left;">姓名</th>
                <th style="padding: 10px; text-align: left;">状态</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
    """
    return html


def build_sign_result_text(results, users, elapsed):
    """构建纯文本结果（用于Server酱）"""
    success_count = sum(1 for r in results if r['success'])
    total = len(results)
    lines = [
        f"## 签到结果",
        f"",
        f"- 总人数：{total}",
        f"- 成功：{success_count}",
        f"- 失败：{total - success_count}",
        f"- 耗时：{elapsed:.1f}秒",
        f"",
        f"### 详细",
        f"",
        f"| 学号 | 姓名 | 状态 |",
        f"|------|------|------|",
    ]
    for user, result in zip(users, results):
        status = "✅ 成功" if result['success'] else f"❌ 失败（{'、'.join(result['data']) if result['data'] else '未知'}）"
        lines.append(f"| {user.student_Id} | {user.username or '未知'} | {status} |")

    return "\n".join(lines)
