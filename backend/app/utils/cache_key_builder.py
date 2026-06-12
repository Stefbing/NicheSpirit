"""
缓存Key命名规范 - 统一缓存Key生成规则

命名规则：
1. 用户相关：user:{user_id}:{data_type}
2. 设备相关（基于设备MAC/UUID）：device:{platform}:{device_mac}:{data_type}
3. 分享相关：share:{share_token}:{data_type}
4. 会话相关：session:{platform}:user_{user_id}
5. Dashboard相关：dashboard:user_{user_id}
"""
import hashlib
import re


def build_user_cache_key(user_id: int, data_type: str) -> str:
    """
    构建用户缓存Key
    Args:
        user_id: 用户ID
        data_type: 数据类型（如：petkit_devices、cloudpets_servings、shared_creds等）
    Returns:
        缓存Key字符串
    """
    return f"user:{user_id}:{data_type}"


def build_device_cache_key(platform: str, device_mac: str, data_type: str) -> str:
    """
    构建设备缓存Key（基于设备MAC地址或UUID）
    Args:
        platform: 设备平台（petkit/cloudpets/xiaomi）
        device_mac: 设备MAC地址或UUID（格式：AA:BB:CC:DD:EE:FF 或 336704）
        data_type: 数据类型（如：status、devices、servings、plans等）
    Returns:
        缓存Key字符串
    """
    # MAC地址格式化（去除冒号、转为小写）
    if ':' in device_mac:
        # MAC地址格式：AA:BB:CC:DD:EE:FF
        mac_normalized = device_mac.replace(':', '').lower()
    else:
        # UUID格式：336704
        mac_normalized = device_mac.lower()
    
    return f"device:{platform}:{mac_normalized}:{data_type}"


def build_share_cache_key(share_token: str, data_type: str = "detail") -> str:
    """
    构建分享缓存Key
    Args:
        share_token: 分享令牌
        data_type: 数据类型（detail、list等）
    Returns:
        缓存Key字符串
    """
    return f"share:{share_token}:{data_type}"


def build_session_cache_key(platform: str, user_id: int) -> str:
    """
    构建会话缓存Key
    Args:
        platform: 设备平台（petkit/cloudpets）
        user_id: 用户ID
    Returns:
        缓存Key字符串
    """
    return f"session:{platform}:user_{user_id}"


def build_dashboard_cache_key(user_id: int) -> str:
    """
    构建Dashboard缓存Key
    Args:
        user_id: 用户ID
    Returns:
        缓存Key字符串
    """
    return f"dashboard:user_{user_id}"


def build_petkit_stats_cache_key(user_id: int, device_id: str) -> str:
    """
    构建PetKit设备统计缓存Key（基于设备ID）
    Args:
        user_id: 用户ID
        device_id: PetKit设备ID
    Returns:
        缓存Key字符串
    """
    return f"user:{user_id}:petkit_stats:{device_id}"


def build_shared_creds_cache_key(user_id: int) -> str:
    """
    构建共享凭据缓存Key
    Args:
        user_id: 用户ID
    Returns:
        缓存Key字符串
    """
    return f"user:{user_id}:shared_creds"


def build_platform_first_user_cache_key(platform: str) -> str:
    """
    构建平台首个用户缓存Key
    Args:
        platform: 设备平台（petkit/cloudpets/xiaomi）
    Returns:
        缓存Key字符串
    """
    return f"platform:{platform}:first_user"


def parse_cache_key(key: str) -> dict:
    """
    解析缓存Key，返回关键信息
    Args:
        key: 缓存Key字符串
    Returns:
        包含key类型和相关信息的字典
    """
    result = {"raw_key": key, "key_type": "unknown"}
    
    # 用户相关
    user_pattern = r"^user:(\d+):(.+)$"
    match = re.match(user_pattern, key)
    if match:
        result["key_type"] = "user"
        result["user_id"] = int(match.group(1))
        data_type = match.group(2)
        result["data_type"] = data_type
        # 特殊处理 petkit_stats:{device_id}
        if data_type.startswith("petkit_stats:"):
            result["key_type"] = "petkit_stats"
            result["device_id"] = data_type.split(":", 1)[1]
        return result
    
    # 设备相关
    device_pattern = r"^device:(.+):(.+):(.+)$"
    match = re.match(device_pattern, key)
    if match:
        result["key_type"] = "device"
        result["platform"] = match.group(1)
        result["device_mac"] = match.group(2)
        result["data_type"] = match.group(3)
        return result
    
    # 分享相关
    share_pattern = r"^share:(.+):(.+)$"
    match = re.match(share_pattern, key)
    if match:
        result["key_type"] = "share"
        result["share_token"] = match.group(1)
        result["data_type"] = match.group(2)
        return result
    
    # 会话相关
    session_pattern = r"^session:(.+):user_(\d+)$"
    match = re.match(session_pattern, key)
    if match:
        result["key_type"] = "session"
        result["platform"] = match.group(1)
        result["user_id"] = int(match.group(2))
        return result
    
    # Dashboard相关
    dashboard_pattern = r"^dashboard:user_(\d+)$"
    match = re.match(dashboard_pattern, key)
    if match:
        result["key_type"] = "dashboard"
        result["user_id"] = int(match.group(1))
        return result
    
    # 平台首个用户
    platform_pattern = r"^platform:(.+):first_user$"
    match = re.match(platform_pattern, key)
    if match:
        result["key_type"] = "platform_first_user"
        result["platform"] = match.group(1)
        return result
    
    return result


# 示例用法
if __name__ == "__main__":
    # 用户缓存Key
    user_key = build_user_cache_key(1, "petkit_devices")
    print(f"用户缓存Key: {user_key}")
    # → "user:1:petkit_devices"

    # 设备缓存Key（PetKit设备，MAC地址：AA:BB:CC:DD:EE:FF）
    device_key = build_device_cache_key("petkit", "AA:BB:CC:DD:EE:FF", "status")
    print(f"设备缓存Key: {device_key}")
    # → "device:petkit:aabbccddeeff:status"

    # 设备缓存Key（CloudPets设备，UUID：336704）
    device_key2 = build_device_cache_key("cloudpets", "336704", "servings")
    print(f"设备缓存Key2: {device_key2}")
    # → "device:cloudpets:336704:servings"

    # 分享缓存Key
    share_key = build_share_cache_key("abc123", "detail")
    print(f"分享缓存Key: {share_key}")
    # → "share:abc123:detail"

    # 会话缓存Key
    session_key = build_session_cache_key("petkit", 1)
    print(f"会话缓存Key: {session_key}")
    # → "session:petkit:user_1"

    # Dashboard缓存Key
    dashboard_key = build_dashboard_cache_key(1)
    print(f"Dashboard缓存Key: {dashboard_key}")
    # → "dashboard:user_1"

    # 解析缓存Key
    parsed = parse_cache_key(user_key)
    print(f"解析结果: {parsed}")
