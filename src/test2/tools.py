"""
演示工具：计算器 + 天气查询
计算器：本地运行
天气：调用和风天气 API 获取实时数据
"""

import math
import os
import requests
from langchain_core.tools import tool


@tool
def calculator(expression: str) -> str:
    """计算数学表达式。

    支持: +, -, *, /, **, sqrt, sin, cos, tan, log, pi, e
    示例: "2 + 3 * 4", "sqrt(16)", "sin(pi/2)", "2**10"
    """
    # 安全的数学环境，禁止任意代码执行
    safe_ns = {
        "__builtins__": {},
        "sqrt": math.sqrt,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "log": math.log,
        "log2": math.log2,
        "log10": math.log10,
        "abs": abs,
        "round": round,
        "pi": math.pi,
        "e": math.e,
    }
    try:
        result = eval(expression, safe_ns)
        return str(result)
    except Exception as ex:
        return f"计算错误: {ex}"


@tool
def weather(city: str) -> str:
    """查询指定城市的当前实时天气（调用和风天气 API）。

    Args:
        city: 城市名称，如 "北京"、"上海"、"深圳"
    """
    api_host = os.getenv("QWEATHER_API_HOST")
    api_key = os.getenv("QWEATHER_API_KEY")
    if not api_host or not api_key:
        return "天气查询失败：未配置 QWEATHER_API_HOST 或 QWEATHER_API_KEY"

    headers = {"X-QW-Api-Key": api_key}

    try:
        # 1. 城市搜索：中文名 → LocationID
        geo_url = f"https://{api_host}/geo/v2/city/lookup"
        #requests.get() 方法用于向指定的 URL 发送 GET 请求，并返回一个 Response 对象。
        geo_resp = requests.get(geo_url, params={"location": city, "range": "cn", "number": 1}, headers=headers, timeout=5)
        
        geo_data = geo_resp.json()
        if geo_data.get("code") != "200" or not geo_data.get("location"):
            return f"未找到城市「{city}」，请检查城市名称"
        city_info = geo_data["location"][0]
        city_id = city_info["id"]
        city_name = city_info["name"]
        adm = city_info.get("adm1", "")

        # 2. 实时天气
        weather_url = f"https://{api_host}/v7/weather/now"
        weather_resp = requests.get(weather_url, params={"location": city_id, "lang": "zh"}, headers=headers, timeout=5)
        data = weather_resp.json()
        if data.get("code") != "200":
            return f"天气查询失败：API 返回错误码 {data.get('code')}"

        now = data["now"]
        return (
            f"{adm} {city_name}：{now['text']}，"
            f"气温 {now['temp']}°C，体感 {now['feelsLike']}°C，"
            f"湿度 {now['humidity']}%，{now['windDir']} {now['windScale']}级"
        )

    except requests.exceptions.Timeout:
        return f"天气查询超时，请稍后重试"
    except requests.exceptions.RequestException as e:
        return f"天气查询网络错误：{e}"
    except (KeyError, IndexError) as e:
        return f"天气数据解析错误：{e}"


# 所有工具列表（供 graph.py 使用）
TOOLS = [calculator, weather]

# 工具名称 → 工具对象映射（供 act 节点快速查找）
TOOL_MAP = {t.name: t for t in TOOLS}
