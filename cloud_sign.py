# -*- coding: utf-8 -*-
"""
安徽工业大学晚寝自动签到 - 云端运行版
从环境变量读取配置，适合 GitHub Actions / 云函数 / 定时任务 等无人值守场景

环境变量：
  STUDENT_IDS  - 学号列表，多个用英文分号分隔（必填）
  PASSWORDS    - 密码列表，多个用英文分号分隔（可选，默认 Ahgydx@920）
  DEBUG_MODE   - 是否忽略签到时间限制（可选，true/false，默认 false）

【邮件通知】（可选）
  EMAIL_ENABLED   - 是否启用邮件通知（true/false，默认 false）
  EMAIL_SMTP      - SMTP服务器（默认 smtp.qq.com，163邮箱用 smtp.163.com）
  EMAIL_PORT      - SMTP端口（默认 465）
  EMAIL_SENDER    - 发件人邮箱
  EMAIL_AUTH_CODE - 发件人邮箱授权码（不是登录密码）
  EMAIL_RECEIVER  - 收件人邮箱（接收签到结果）

【Server酱微信推送】（可选）
  SERVERCHAN_ENABLED - 是否启用Server酱（true/false，默认 false）
  SERVERCHAN_SENDKEY - Server酱 SendKey

示例（单用户）：
  STUDENT_IDS=259000000
  PASSWORDS=Ahgydx@920

示例（多用户）：
  STUDENT_IDS=259000000;259000001;259000002
  PASSWORDS=Ahgydx@920;mypassword1;Ahgydx@920
"""
import base64
import json
import os
import sys
from datetime import datetime, timezone, timedelta
import hashlib
import logging
import random
import time
import asyncio
import aiohttp
from dataclasses import dataclass
from urllib.parse import urlparse

# 导入通知模块
from notifier import Notifier, build_sign_result_content, build_sign_result_text

# ============================================================
# 配置
# ============================================================

API_BASE_URL = "https://xskq.ahut.edu.cn/api"
WEB_DICT = {
    "token_api": f"{API_BASE_URL}/flySource-auth/oauth/token",
    "task_id_api": f"{API_BASE_URL}/flySource-yxgl/dormSignTask/getStudentTaskPage?userDataType=student&current=1&size=15",
    "auth_check_api": f"{API_BASE_URL}/flySource-base/wechat/getWechatMpConfig"
                      "?configUrl=https://xskq.ahut.edu.cn/wise/pages/ssgl/dormsign"
                      "?taskId={TASK_ID}&autoSign=1&scanSign=0&userId={STUDENT_ID}",
    "apiLog_api": f"{API_BASE_URL}/flySource-base/apiLog/save?menuTitle=%E6%99%9A%E5%AF%9D%E7%AD%BE%E5%88%B0",
    'get_location_api': f"{API_BASE_URL}/flySource-yxgl/dormSignTask/getTaskByIdForApp"
                        "?taskId={TASK_ID}&signDate={date_str}",
    "sign_in_api": f"{API_BASE_URL}/flySource-yxgl/dormSignRecord/stuSign",
    "sign_in_result_api": f"{API_BASE_URL}/flySource-yxgl/dormSignStu/getWqdStudentPage"
                          "?taskId={TASK_ID}&xhOrXm=&nowDate={date_str}&userDataType=student&current=1&size=100",
}

UA_LIST = [
    "Mozilla/5.0 (Linux; Android 15; MIX Fold 4 Build/TKQ1.240502.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/128.0.6613.137 Mobile Safari/537.36 MicroMessenger/8.0.61.2660(0x28003D37) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64",
    "Mozilla/5.0 (Linux; Android 15; LYA-AL10 Build/HUAWEILYA-AL10; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/128.0.6613.137 Mobile Safari/537.36 MicroMessenger/8.0.61.2660(0x28003D37) WeChat/arm64 Weixin NetType/5G Language/zh_CN ABI/arm64",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.61(0x18003D29) NetType/WIFI Language/zh_CN",
]

MAX_RETRIES = 4
MAX_TOKEN_RETRIES = 3
MAX_CONCURRENT = 5  # 云端降低并发，更稳定

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


@dataclass
class User:
    student_Id: int
    username: str = ''
    password: str = "Ahgydx@920"
    latitude: float = 0
    longitude: float = 0
    token: str = None
    taskId: int = None
    room_id: str = ""
    is_encrypted: int = 0
    _session = None

    @property
    def session(self):
        if self._session is None:
            session = aiohttp.ClientSession(headers={
                'User-Agent': random.choice(UA_LIST),
                'authorization': "Basic Zmx5c291cmNlX3dpc2VfYXBwOkRBNzg4YXNkVURqbmFzZF9mbHlzb3VyY2VfZHNkYWREQUlVaXV3cWU=",
                'Content-Type': "application/json;charset=UTF-8",
                'X-Requested-With': "com.tencent.mm",
                'Origin': "https://xskq.ahut.edu.cn",
                'Referer': f"https://xskq.ahut.edu.cn/wise/pages/ssgl/dormsign?&userId={self.student_Id}"
            })
            self._session = session
        else:
            if self.token:
                self._session.headers["flysource-auth"] = f"bearer {self.token}"
        return self._session

    async def close(self):
        if self._session:
            await self._session.close()


def password_md5(pwd: str) -> str:
    return hashlib.md5(pwd.encode('utf-8')).hexdigest()


def generate_sign(url, token) -> str:
    if not token:
        return ''
    parsed_url = urlparse(url)
    api = parsed_url.path + "?sign="
    timestamp = int(time.time() * 1000)
    inner = f"{timestamp}{token}"
    inner_hash = hashlib.md5(inner.encode("utf-8")).hexdigest()
    raw = f"{api}{inner_hash}"
    final_hash = hashlib.md5(raw.encode("utf-8")).hexdigest()
    encoded_time = base64.b64encode(str(timestamp).encode("utf-8")).decode("utf-8")
    return f"{final_hash}1.{encoded_time}"


def get_time() -> dict:
    now = time.localtime()
    return {
        "date": time.strftime("%Y-%m-%d", now),
        "time": time.strftime("%H:%M:%S", now),
        "full": time.strftime("%Y年%m月%d日 %H:%M:%S", now),
    }


def generate_header(user: User, url: str = None) -> dict:
    header = {}
    if user.token:
        header['flysource-auth'] = f"bearer {user.token}"
        if url:
            header['flysource-sign'] = generate_sign(url, user.token)
    return header


def generate_params(user: User):
    return {
        'tenantId': '000000',
        'username': user.student_Id,
        'password': user.password if user.is_encrypted else password_md5(user.password),
        'type': 'account',
        'grant_type': 'password',
        'scope': 'all'
    }


def generate_signCode(timestamp_ms):
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc) + timedelta(hours=8)
    week = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    w = week[dt.weekday()]
    m = month[dt.month - 1]
    tz = "GMT+0800 (中国标准时间)"
    time_str = f"{w} {m} {dt.day:02d} {dt.year} {dt.strftime('%H:%M:%S')} {tz}"
    return hashlib.md5(time_str.encode()).hexdigest()


def generate_stuTaskId(lat, lng, acc, date, taskId, fileId=""):
    data = {
        "latitude": str(lat),
        "longitude": str(lng),
        "locationAccuracy": str(acc),
        "signDate": date,
        "taskId": taskId,
        "fileId": fileId
    }
    json_str = json.dumps(data, separators=(',', ':'))
    return hashlib.md5(json_str.encode()).hexdigest()


def generate_data(user: User) -> dict:
    signLat = user.latitude + round(random.uniform(-0.01, 0.01), 6)
    signLng = user.longitude + round(random.uniform(-0.01, 0.01), 6)
    locationAccuracy = round(random.uniform(25, 35), 2)
    return {
        "signType": 0,
        "taskId": user.taskId,
        "signLat": signLat,
        "signLng": signLng,
        "locationAccuracy": locationAccuracy,
        "stuTaskId": generate_stuTaskId(signLat, signLng, locationAccuracy, get_time()['date'], user.taskId),
        "scanCode": "",
        "scanType": "",
        "roomId": user.room_id,
        "signKey": user.room_id,
        "signCode": generate_signCode(int(time.time())),
    }


async def sign_in_by_step(user: User, step: int, debug: bool = False, sign_lock=None) -> dict:
    if not debug:
        now_time = get_time()['time']
        if now_time < '21:20:00':
            logger.error(f'当前时间 {now_time} 未到签到时间（21:20后）')
            return {'success': False, 'msg': "未到签到时间", 'step': -1}

    if step == 0:
        logger.info(f"[{user.student_Id}] 1/6 获取登录凭证...")
        async with user.session.post(
                url=WEB_DICT["token_api"],
                params=generate_params(user),
                headers=generate_header(user)
        ) as resp:
            token_result = await resp.json()
        if 'refresh_token' in token_result:
            user.token = token_result['refresh_token']
            user.username = token_result['userName']
            logger.info(f"[{user.student_Id}] 凭证获取成功：{user.username}")
            return {'success': True, 'msg': '', 'step': step + 1}
        else:
            error_desc = token_result.get('error_description', '未知错误')
            if "Bad credentials" in error_desc or "用户名或密码错误" in error_desc:
                error_desc = "学号或密码错误"
            logger.error(f"[{user.student_Id}] 凭证获取失败：{error_desc}")
            return {'success': False, 'msg': error_desc, 'step': -1}

    if step == 1:
        logger.info(f"[{user.student_Id}] 2/6 获取签到任务ID...")
        async with user.session.get(
                url=WEB_DICT['task_id_api'],
                headers=generate_header(user, WEB_DICT['task_id_api'])
        ) as resp:
            task_result = await resp.json()
        if task_result['code'] == 200:
            records = task_result.get('data', {}).get('records', [{}])
            if records and records[0].get("taskId"):
                user.taskId = records[0].get("taskId")
                logger.info(f"[{user.student_Id}] 任务ID：{user.taskId}")
                return {'success': True, 'msg': '', 'step': step + 1}
            else:
                logger.error(f"[{user.student_Id}] 未找到今日签到任务")
                return {'success': False, 'msg': '未找到签到任务', 'step': step}
        else:
            msg = task_result.get('msg', '')
            if any(k in msg for k in ["请求未授权", "缺失身份信息", "鉴权失败"]):
                logger.warning(f"[{user.student_Id}] 凭证失效，重新获取")
                user.token = ''
                return {'success': False, 'msg': 'token失效', 'step': 0}
            logger.error(f"[{user.student_Id}] 获取任务ID出错：{msg}")
            return {'success': False, 'msg': msg, 'step': step}

    if step == 2:
        logger.info(f"[{user.student_Id}] 3/6 验证微信环境...")
        url = WEB_DICT['auth_check_api'].format(TASK_ID=user.taskId, STUDENT_ID=user.student_Id)
        async with user.session.get(url=url, headers=generate_header(user, url)) as resp:
            auth_result = await resp.json()
        if auth_result['code'] == 200:
            logger.info(f"[{user.student_Id}] 微信环境验证通过")
            return {'success': True, 'msg': '', 'step': step + 1}
        else:
            msg = auth_result.get('msg', '')
            if any(k in msg for k in ["请求未授权", "缺失身份信息", "鉴权失败"]):
                user.token = ''
                return {'success': False, 'msg': 'token失效', 'step': 0}
            logger.error(f"[{user.student_Id}] 微信环境验证出错：{msg}")
            return {'success': False, 'msg': msg, 'step': step}

    if step == 3:
        logger.info(f"[{user.student_Id}] 4/6 开启签到时间窗口...")
        async with user.session.post(
                url=WEB_DICT["apiLog_api"],
                headers=generate_header(user, WEB_DICT['apiLog_api'])
        ) as resp:
            if resp.status == 200:
                logger.info(f"[{user.student_Id}] 时间窗口已开启")
                return {'success': True, 'msg': '', 'step': step + 1}
            logger.error(f"[{user.student_Id}] 开启时间窗口失败")
            return {'success': False, 'msg': "开启签到时间窗口失败", 'step': step}

    if step == 4:
        logger.info(f"[{user.student_Id}] 5/6 获取签到位置...")
        url = WEB_DICT['get_location_api'].format(TASK_ID=user.taskId, date_str=datetime.now().strftime('%Y-%m-%d'))
        async with user.session.get(url, headers=generate_header(user, url)) as resp:
            location_result = await resp.json()
        if location_result['code'] == 200:
            dorm = location_result['data'].get('dormitoryRegisterVO', {})
            user.latitude = float(dorm.get('locationLat', 0))
            user.longitude = float(dorm.get('locationLng', 0))
            user.room_id = dorm.get("roomId", "")
            logger.info(f"[{user.student_Id}] 位置获取成功（宿舍：{user.room_id or '未知'}）")
            return {'success': True, 'msg': '', 'step': step + 1}
        else:
            msg = location_result.get('msg', '')
            if any(k in msg for k in ["请求未授权", "缺失身份信息", "鉴权失败"]):
                user.token = ''
                return {'success': False, 'msg': 'token失效', 'step': 0}
            return {"success": False, "msg": "", "step": step + 1}

    if step == 5:
        async with sign_lock:
            logger.info(f"[{user.student_Id}] 6/6 提交签到（模拟定位偏差中...）")
            sleep_time = round(random.uniform(4, 10))
            await asyncio.sleep(sleep_time)
            async with user.session.post(
                    url=WEB_DICT["sign_in_api"],
                    json=generate_data(user),
                    headers=generate_header(user, WEB_DICT['sign_in_api'])
            ) as resp:
                sign_in_result = await resp.json()
            if sign_in_result['code'] == 200 or '您今天已完成签到' in sign_in_result.get('msg', ''):
                logger.info(f"[{user.student_Id}] 签到成功！")
                return {'success': True, 'msg': '', 'step': step + 1}
            else:
                msg = sign_in_result.get('msg', '')
                if any(k in msg for k in ["请求未授权", "缺失身份信息", "鉴权失败"]):
                    user.token = ''
                    return {'success': False, 'msg': 'token失效', 'step': 0}
                if '未到签到时间' in msg:
                    logger.error(f"[{user.student_Id}] 未到签到时间")
                    return {'success': False, 'msg': msg, 'step': -1}
                logger.error(f"[{user.student_Id}] 签到提交出错：{msg}")
                return {'success': False, 'msg': msg, 'step': step}

    return {'success': False, 'msg': '', 'step': -1}


async def sign_in(user: User, debug: bool = False, sign_lock=None):
    step, retries, token_retries = 0, 0, 0
    error_history = set()
    while retries < MAX_RETRIES and 0 <= step < 6:
        result = await sign_in_by_step(user, step, debug, sign_lock)
        step = result['step']
        if not result['success']:
            if result['msg']:
                error_history.add(result['msg'])
            if step == 0 and token_retries < MAX_TOKEN_RETRIES:
                token_retries += 1
            else:
                retries += 1
        await asyncio.sleep(round(random.uniform(0.5, 2), 2))
    if step == 6:
        return {'success': True, 'data': error_history}
    else:
        return {'success': False, 'data': error_history}


def load_users_from_env():
    """从环境变量加载用户列表"""
    student_ids_str = os.environ.get("STUDENT_IDS", "").strip()
    passwords_str = os.environ.get("PASSWORDS", "").strip()

    if not student_ids_str:
        logger.error("环境变量 STUDENT_IDS 未设置！")
        return []

    student_ids = [s.strip() for s in student_ids_str.split(";") if s.strip()]
    passwords = [p.strip() for p in passwords_str.split(";") if passwords_str]

    users = []
    for i, sid in enumerate(student_ids):
        pwd = passwords[i] if i < len(passwords) else "Ahgydx@920"
        if not sid.isdigit():
            logger.warning(f"学号格式不正确，跳过：{sid}")
            continue
        users.append(User(student_Id=int(sid), password=pwd))
        logger.info(f"已加载用户：{sid}")

    return users


async def main():
    logger.info("=" * 50)
    logger.info("安徽工业大学晚寝自动签到 - 云端版启动")
    logger.info(f"当前时间：{get_time()['full']}")
    logger.info("=" * 50)

    users = load_users_from_env()
    if not users:
        logger.error("没有可用的用户，退出")
        sys.exit(1)

    debug_mode = (os.environ.get("DEBUG_MODE") or "false").lower() == "true"
    if debug_mode:
        logger.warning("调试模式已开启：忽略签到时间限制")

    sign_lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def limited_sign_in(user):
        async with semaphore:
            return await sign_in(user, debug=debug_mode, sign_lock=sign_lock)

    logger.info(f"开始为 {len(users)} 人执行签到...")
    start_time = time.time()

    results = await asyncio.gather(*(limited_sign_in(u) for u in users))
    await asyncio.gather(*[user.close() for user in users])

    end_time = time.time()
    success_count = sum(1 for r in results if r['success'])

    logger.info("=" * 50)
    logger.info(f"签到完成：共 {len(users)} 人，成功 {success_count} 人，失败 {len(users) - success_count} 人")
    logger.info(f"总耗时：{end_time - start_time:.2f} 秒")

    for user, result in zip(users, results):
        status = "成功" if result['success'] else "失败"
        errors = "、".join(result['data']) if result['data'] else "无"
        logger.info(f"  {user.student_Id} ({user.username or '未知'}): {status} - {errors}")

    logger.info("=" * 50)

    # ---------- 发送通知 ----------
    elapsed = end_time - start_time
    all_success = success_count == len(users)

    if all_success:
        title = f"✅ 签到全部成功（{success_count}/{len(users)}）"
    else:
        title = f"⚠️ 签到部分失败（{success_count}/{len(users)}）"

    # 从环境变量构建通知配置（用or处理空字符串，避免未配置Secret时报错）
    notify_config = {
        "email": {
            "enabled": (os.environ.get("EMAIL_ENABLED") or "false").lower() == "true",
            "smtp_server": os.environ.get("EMAIL_SMTP") or "smtp.qq.com",
            "smtp_port": int(os.environ.get("EMAIL_PORT") or "465"),
            "sender": os.environ.get("EMAIL_SENDER") or "",
            "auth_code": os.environ.get("EMAIL_AUTH_CODE") or "",
            "receiver": os.environ.get("EMAIL_RECEIVER") or "",
        },
        "serverchan": {
            "enabled": (os.environ.get("SERVERCHAN_ENABLED") or "false").lower() == "true",
            "sendkey": os.environ.get("SERVERCHAN_SENDKEY") or "",
        }
    }

    notifier = Notifier(notify_config)
    if notify_config["email"]["enabled"] or notify_config["serverchan"]["enabled"]:
        logger.info("正在发送签到结果通知...")
        html_content = build_sign_result_content(results, users, elapsed)
        text_content = build_sign_result_text(results, users, elapsed)
        # 邮件用HTML，Server酱用Markdown文本
        # Notifier.send 统一发送，这里分别处理
        if notify_config["email"]["enabled"]:
            email_ok = notifier._send_email(title, html_content)
            logger.info(f"邮件通知：{'成功' if email_ok else '失败'}")
        if notify_config["serverchan"]["enabled"]:
            sc_ok = notifier._send_serverchan(title, text_content)
            logger.info(f"Server酱推送：{'成功' if sc_ok else '失败'}")
    else:
        logger.info("未启用任何通知渠道，跳过通知发送")

    # 如果有失败的，返回非零退出码（方便GitHub Actions识别）
    if success_count < len(users):
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
