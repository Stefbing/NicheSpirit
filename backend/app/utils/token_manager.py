"""
Token管理模块 — 统一的Token签发、刷新、续期逻辑

设计目标：
1. 统一Token管理：所有Token操作通过此模块
2. Sliding Window机制：活跃用户的Token自动续期
3. 清晰的配置：所有Token相关配置集中管理
4. 安全的Token生成：使用secrets.token_urlsafe()
5. 完整的日志：Token生命周期可追溯

Token生命周期：
    登录/注册
        ↓
   生成Token (7天过期)
        ↓
   每次API调用 → get_current_user()
        ↓
   检查过期时间
        ↓
   如果距离过期 < 24h → 自动续期到完整7天
        ↓
   用户活跃 → Token永不过期
   用户不活跃 > 7天 → Token过期 → 需要重新登录

使用示例：
    from backend.app.utils.token_manager import TokenManager
    
    # 生成Token
    token = await TokenManager.generate_token(user_id=123, session=session)
    
    # 刷新Token（使用旧Token换取新Token）
    new_token = await TokenManager.refresh_token(old_token, session)
    
    # 验证Token（在get_current_user中使用）
    user = await TokenManager.validate_token(token, session)
"""

import secrets
import time
import hashlib
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

from sqlmodel import Session, select
from backend.app.models.models import User

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== Token配置常量 ====================

# Token过期时间（小时）
# 默认7天，可通过环境变量TOKEN_EXPIRE_HOURS覆盖
_TOKEN_EXPIRE_HOURS = 168  # 7天

# Token自动续期阈值（毫秒）
# 当Token距离过期不足此值时，自动续期
# 默认24小时，保证活跃用户几乎不会遇到401
_TOKEN_REFRESH_THRESHOLD_MS = 24 * 3600 * 1000

# Token哈希算法
_TOKEN_HASH_ALGORITHM = "SHA256"


class TokenManager:
    """
    Token管理器 - 统一的Token操作入口
    
    此类包含所有Token相关的操作：
    - 生成Token
    - 刷新Token
    - 验证Token
    - 撤销Token
    
    所有方法都是类方法（@classmethod），不需要实例化。
    """
    
    # ==================== 配置管理 ====================
    
    @classmethod
    def get_token_expire_hours(cls) -> int:
        """
        获取Token过期时间（小时）
        
        Returns:
            int: 过期小时数
            
        示例：
            >>> hours = TokenManager.get_token_expire_hours()
            >>> print(f"Token expires in {hours} hours")
        """
        import os
        try:
            return int(os.getenv("TOKEN_EXPIRE_HOURS", _TOKEN_EXPIRE_HOURS))
        except ValueError:
            return _TOKEN_EXPIRE_HOURS
    
    @classmethod
    def get_token_expire_ms(cls) -> int:
        """
        获取Token过期时间（毫秒）
        
        Returns:
            int: 过期毫秒数
        """
        return cls.get_token_expire_hours() * 3600 * 1000
    
    @classmethod
    def get_refresh_threshold_ms(cls) -> int:
        """
        获取Token自动续期阈值（毫秒）
        
        默认24小时，或Token生命周期的1/4（取较小值）
        
        Returns:
            int: 续期阈值（毫秒）
        """
        token_lifetime_ms = cls.get_token_expire_ms()
        # 取24小时和Token生命周期1/4的较小值
        default_threshold = min(_TOKEN_REFRESH_THRESHOLD_MS, token_lifetime_ms // 4)
        
        import os
        try:
            return int(os.getenv("TOKEN_REFRESH_THRESHOLD_MS", default_threshold))
        except ValueError:
            return default_threshold
    
    # ==================== Token生成 ====================
    
    @classmethod
    def hash_token(cls, token: str) -> str:
        """
        Token哈希（存储用）
        
        将原始Token哈希后存储到数据库，避免明文存储。
        使用SHA256算法。
        
        Args:
            token: 原始Token字符串
            
        Returns:
            str: 哈希后的Token（十六进制字符串）
            
        示例：
            >>> token = "abc123..."
            >>> token_hash = TokenManager.hash_token(token)
            >>> print(token_hash)  # SHA256哈希值
        """
        return hashlib.sha256(token.encode('utf-8')).hexdigest()
    
    @classmethod
    async def generate_token(cls, user_id: int, session: Session) -> str:
        """
        生成新Token并存储到数据库
        
        此方法：
        1. 生成32字节的随机Token（URL安全的Base64）
        2. 计算Token哈希
        3. 计算过期时间（当前时间 + 配置过期时间）
        4. 更新用户的token_hash和token_expires_at字段
        5. 返回原始Token
        
        Args:
            user_id: 用户ID
            session: 数据库Session
            
        Returns:
            str: 原始Token（非哈希）
            
        Raises:
            ValueError: 用户不存在
            
        示例：
            >>> token = await TokenManager.generate_token(user_id=123, session=session)
            >>> print(f"New token: {token}")
        """
        # 1. 生成随机Token
        raw_token = secrets.token_urlsafe(32)
        token_hash = cls.hash_token(raw_token)
        
        # 2. 计算过期时间
        now_ms = int(time.time() * 1000)
        expire_ms = now_ms + cls.get_token_expire_ms()
        
        # 3. 更新用户记录
        user = session.exec(select(User).where(User.id == user_id)).first()
        if not user:
            raise ValueError(f"User not found: user_id={user_id}")
        
        user.token_hash = token_hash
        user.token_expires_at = expire_ms
        user.updated_at = now_ms
        session.add(user)
        session.commit()
        
        logger.info(
            f"[Token] 生成Token: user_id={user_id}, "
            f"expire_at={datetime.fromtimestamp(expire_ms / 1000)}"
        )
        
        return raw_token
    
    # ==================== Token验证 ====================
    
    @classmethod
    async def validate_token(cls, token: str, session: Session) -> Optional[User]:
        """
        验证Token并返回用户对象
        
        此方法的逻辑：
        1. 计算Token哈希
        2. 查询数据库匹配的记录
        3. 检查Token是否过期
        4. 如果Token即将过期，自动续期（Sliding Window）
        5. 返回用户对象
        
        Args:
            token: 原始Token字符串
            session: 数据库Session
            
        Returns:
            User对象，或None（Token无效或已过期）
            
        示例：
            >>> user = await TokenManager.validate_token(token, session)
            >>> if user:
            ...     print(f"Authenticated: {user.id}")
        """
        # 1. 计算Token哈希
        token_hash = cls.hash_token(token)
        now_ms = int(time.time() * 1000)
        
        # 2. 查询用户
        user = session.exec(
            select(User).where(User.token_hash == token_hash)
        ).first()
        
        if not user:
            logger.warning(f"[Token] 验证失败：Token不存在")
            return None
        
        # 3. 检查过期
        if user.token_expires_at is None or user.token_expires_at <= now_ms:
            logger.warning(f"[Token] 验证失败：Token已过期 user_id={user.id}")
            return None
        
        # 4. Token自动续期（Sliding Window）
        try:
            refresh_threshold_ms = cls.get_refresh_threshold_ms()
            if user.token_expires_at - now_ms < refresh_threshold_ms:
                # Token即将过期，自动续期
                user.token_expires_at = now_ms + cls.get_token_expire_ms()
                user.updated_at = now_ms
                session.add(user)
                session.commit()
                logger.info(
                    f"[Token] 自动续期: user_id={user.id}, "
                    f"new_expire_at={datetime.fromtimestamp(user.token_expires_at / 1000)}"
                )
        except Exception as e:
            # 续期失败不阻塞请求（非致命错误）
            logger.warning(f"[Token] 续期失败（非致命）: {e}")
        
        return user
    
    # ==================== Token刷新 ====================
    
    @classmethod
    async def refresh_token(cls, old_token: str, session: Session) -> Optional[str]:
        """
        刷新Token（使用旧Token换取新Token）
        
        此方法的典型使用场景：
        1. 前端检测到401响应（TOKEN_EXPIRED）
        2. 前端调用POST /api/auth/refresh接口
        3. 后端验证旧Token（必须未过期）
        4. 生成新Token并返回
        
        Args:
            old_token: 旧Token（原始字符串）
            session: 数据库Session
            
        Returns:
            新Token字符串，或None（旧Token无效或已过期）
            
        示例：
            >>> new_token = await TokenManager.refresh_token(old_token, session)
            >>> if new_token:
            ...     print(f"New token: {new_token}")
        """
        # 1. 验证旧Token
        user = await cls.validate_token(old_token, session)
        if not user:
            logger.warning(f"[Token] 刷新失败：旧Token无效或已过期")
            return None
        
        # 2. 生成新Token
        new_token = await cls.generate_token(user.id, session)
        
        logger.info(f"[Token] Token刷新成功: user_id={user.id}")
        
        return new_token
    
    # ==================== Token撤销 ====================
    
    @classmethod
    async def revoke_token(cls, user_id: int, session: Session) -> bool:
        """
        撤销用户的Token（登出）
        
        此方法：
        1. 清除用户的token_hash
        2. 清除token_expires_at
        3. 使现有Token立即失效
        
        Args:
            user_id: 用户ID
            session: 数据库Session
            
        Returns:
            bool: 是否成功
        """
        try:
            user = session.exec(select(User).where(User.id == user_id)).first()
            if not user:
                logger.warning(f"[Token] 撤销失败：用户不存在 user_id={user_id}")
                return False
            
            user.token_hash = None
            user.token_expires_at = None
            user.updated_at = int(time.time() * 1000)
            session.add(user)
            session.commit()
            
            logger.info(f"[Token] Token已撤销: user_id={user_id}")
            return True
        except Exception as e:
            logger.error(f"[Token] 撤销失败: {e}")
            return False
    
    # ==================== 工具方法 ====================
    
    @classmethod
    def decode_token_payload(cls, token: str) -> Optional[Dict[str, Any]]:
        """
        解码Token负载（如果Token是JWT格式）
        
        注意：当前实现使用随机Token（非JWT），
        此方法保留用于未来升级到JWT格式。
        
        Args:
            token: Token字符串
            
        Returns:
            解码后的负载字典，或None
        """
        # TODO: 未来如果升级到JWT，实现此方法
        logger.warning("[Token] decode_token_payload: 当前Token格式不支持解码（非JWT）")
        return None
    
    @classmethod
    def get_token_info(cls, user: User) -> Dict[str, Any]:
        """
        获取Token信息
        
        Args:
            user: User对象
            
        Returns:
            Token信息字典
            
        示例：
            >>> user = session.exec(select(User).where(User.id == 123)).first()
            >>> info = TokenManager.get_token_info(user)
            >>> print(info['expire_at'])
        """
        now_ms = int(time.time() * 1000)
        
        if not user.token_expires_at:
            return {
                'has_token': False,
                'is_expired': True,
                'expire_at': None,
            }
        
        is_expired = user.token_expires_at <= now_ms
        time_until_expiry = max(0, user.token_expires_at - now_ms)
        
        return {
            'has_token': True,
            'is_expired': is_expired,
            'expire_at': datetime.fromtimestamp(user.token_expires_at / 1000).isoformat(),
            'time_until_expiry_ms': time_until_expiry,
            'time_until_expiry_hours': round(time_until_expiry / (3600 * 1000), 2),
        }
