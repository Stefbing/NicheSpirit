"""
Dashboard 缓存管理器 - 优化的缓存策略
实现分层缓存、智能预热、命中率监控
"""
import asyncio
import logging
import time
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# 缓存TTL策略（秒）
CACHE_TTL = {
    # L1: Dashboard整体缓存 - 与数据同步间隔对齐
    'dashboard_overall': 300,  # 5分钟
    
    # L2: 组件缓存 - 比同步间隔长，防止同步失败时数据过期
    'petkit_devices': 600,      # 10分钟
    'cloudpets_servings': 600, # 10分钟
    'cloudpets_plans': 600,    # 10分钟
    'scale_stats': 300,         # 5分钟（数据库查询）
    
    # L3: 用户配置缓存 - 长时间缓存
    'user_platforms': 600,      # 10分钟
    'shared_creds': 600,        # 10分钟
}

# 缓存预热配置
WARMUP_CONFIG = {
    'enabled': True,
    'batch_size': 10,           # 每批预热用户数
    'interval': 60,             # 预热检查间隔（秒）
}


class DashboardCacheManager:
    """
    Dashboard缓存管理器
    
    核心功能：
    1. 分层缓存策略（L1/L2/L3）
    2. 智能缓存预热
    3. 缓存命中率监控
    4. 批量缓存失效
    """
    
    def __init__(self, redis_cache):
        self.redis = redis_cache
        self._warmup_task = None
        self._hit_stats = {
            'overall': {'hit': 0, 'miss': 0},
            'components': {'hit': 0, 'miss': 0},
        }
    
    # =========================================================================
    # 1. 分层缓存策略
    # =========================================================================
    
    async def get_dashboard_cache(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        获取Dashboard整体缓存（L1）
        
        Returns:
            缓存的Dashboard数据，或None（未命中）
        """
        cache_key = f"dashboard:user_{user_id}"
        cached = await self.redis.get(cache_key)
        
        if cached is not None:
            self._hit_stats['overall']['hit'] += 1
            logger.info(f"[DashboardCache] L1 HIT user_id={user_id}")
            return cached
        else:
            self._hit_stats['overall']['miss'] += 1
            logger.info(f"[DashboardCache] L1 MISS user_id={user_id}")
            return None
    
    async def set_dashboard_cache(self, user_id: int, data: Dict[str, Any]) -> bool:
        """
        设置Dashboard整体缓存（L1）
        
        Args:
            user_id: 用户ID
            data: Dashboard数据
            
        Returns:
            是否成功
        """
        try:
            cache_key = f"dashboard:user_{user_id}"
            ttl = CACHE_TTL['dashboard_overall']
            
            await self.redis.set(cache_key, data, ttl=ttl)
            logger.info(f"[DashboardCache] L1 SET user_id={user_id} ttl={ttl}s")
            return True
        except Exception as e:
            logger.error(f"[DashboardCache] L1 SET failed: {e}")
            return False
    
    async def get_component_cache(self, user_id: int, component: str) -> Optional[Any]:
        """
        获取组件缓存（L2）
        
        Args:
            user_id: 用户ID
            component: 组件名（petkit_devices/cloudpets_servings等）
            
        Returns:
            缓存的组件数据，或None（未命中）
        """
        cache_key = f"user_{user_id}_{component}"
        cached = await self.redis.get(cache_key)
        
        if cached is not None:
            self._hit_stats['components']['hit'] += 1
            logger.debug(f"[DashboardCache] L2 HIT component={component} user_id={user_id}")
            return cached
        else:
            self._hit_stats['components']['miss'] += 1
            logger.debug(f"[DashboardCache] L2 MISS component={component} user_id={user_id}")
            return None
    
    async def set_component_cache(self, user_id: int, component: str, data: Any) -> bool:
        """
        设置组件缓存（L2）
        
        Args:
            user_id: 用户ID
            component: 组件名
            data: 组件数据
            
        Returns:
            是否成功
        """
        try:
            cache_key = f"user_{user_id}_{component}"
            ttl = CACHE_TTL.get(component, 300)
            
            await self.redis.set(cache_key, data, ttl=ttl)
            logger.debug(f"[DashboardCache] L2 SET component={component} user_id={user_id} ttl={ttl}s")
            return True
        except Exception as e:
            logger.error(f"[DashboardCache] L2 SET failed: {e}")
            return False
    
    # =========================================================================
    # 2. 智能缓存预热
    # =========================================================================
    
    async def warmup_user_cache(self, user_id: int, dashboard_data: Dict[str, Any]):
        """
        预热指定用户的缓存
        
        在数据同步完成后调用，主动更新Dashboard缓存和组件缓存
        
        Args:
            user_id: 用户ID
            dashboard_data: 完整的Dashboard数据
        """
        if not WARMUP_CONFIG['enabled']:
            return
        
        try:
            # 1. 更新L1：Dashboard整体缓存
            await self.set_dashboard_cache(user_id, dashboard_data)
            
            # 2. 更新L2：各组件缓存
            components = ['petkit_devices', 'cloudpets_servings', 'cloudpets_plans', 'scale_stats']
            for component in components:
                if component in dashboard_data:
                    await self.set_component_cache(user_id, component, dashboard_data[component])
            
            logger.info(f"[DashboardCache] 预热完成 user_id={user_id}")
        except Exception as e:
            logger.error(f"[DashboardCache] 预热失败 user_id={user_id}: {e}")
    
    async def warmup_all_users_cache(self, user_ids: List[int], data_fetcher):
        """
        批量预热所有用户的缓存
        
        Args:
            user_ids: 用户ID列表
            data_fetcher: 数据获取函数 async (user_id) -> dashboard_data
        """
        if not WARMUP_CONFIG['enabled']:
            return
        
        logger.info(f"[DashboardCache] 开始批量预热 {len(user_ids)} 个用户")
        
        # 分批处理，避免并发过高
        batch_size = WARMUP_CONFIG['batch_size']
        for i in range(0, len(user_ids), batch_size):
            batch = user_ids[i:i + batch_size]
            
            # 并发预热当前批次
            tasks = [self._warmup_single_user(uid, data_fetcher) for uid in batch]
            await asyncio.gather(*tasks, return_exceptions=True)
            
            logger.info(f"[DashboardCache] 批次 {i//batch_size + 1} 完成，用户数={len(batch)}")
            
            # 短暂休眠，避免Redis压力过大
            await asyncio.sleep(0.1)
        
        logger.info(f"[DashboardCache] 批量预热完成，总用户数={len(user_ids)}")
    
    async def _warmup_single_user(self, user_id: int, data_fetcher):
        """预热单个用户缓存"""
        try:
            dashboard_data = await data_fetcher(user_id)
            if dashboard_data:
                await self.warmup_user_cache(user_id, dashboard_data)
        except Exception as e:
            logger.warning(f"[DashboardCache] 预热用户失败 user_id={user_id}: {e}")
    
    # =========================================================================
    # 3. 缓存失效管理
    # =========================================================================
    
    async def invalidate_user_cache(self, user_id: int, components: Optional[List[str]] = None):
        """
        失效指定用户的缓存
        
        Args:
            user_id: 用户ID
            components: 要失效的组件列表，None表示失效所有
        """
        try:
            keys_to_delete = []
            
            # L1缓存
            keys_to_delete.append(f"dashboard:user_{user_id}")
            
            # L2缓存
            if components is None:
                components = ['petkit_devices', 'cloudpets_servings', 'cloudpets_plans', 'scale_stats']
            
            for component in components:
                keys_to_delete.append(f"user_{user_id}_{component}")
            
            # 批量删除
            for key in keys_to_delete:
                await self.redis.delete(key)
            
            logger.info(f"[DashboardCache] 失效缓存 user_id={user_id} components={components}")
        except Exception as e:
            logger.error(f"[DashboardCache] 失效缓存失败: {e}")
    
    async def invalidate_all_cache(self):
        """失效所有Dashboard缓存（慎用）"""
        try:
            # 使用pattern删除
            patterns = [
                "dashboard:user_*",
                "user_*_petkit_devices",
                "user_*_cloudpets_servings",
                "user_*_cloudpets_plans",
                "user_*_scale_stats",
            ]
            
            for pattern in patterns:
                await self.redis.delete_pattern(pattern)
            
            logger.warning("[DashboardCache] 已失效所有Dashboard缓存")
        except Exception as e:
            logger.error(f"[DashboardCache] 批量失效失败: {e}")
    
    # =========================================================================
    # 4. 缓存命中率监控
    # =========================================================================
    
    def get_hit_rate_stats(self) -> Dict[str, Any]:
        """
        获取缓存命中率统计
        
        Returns:
            包含各层级缓存命中率的字典
        """
        stats = {}
        
        # L1: Dashboard整体缓存
        overall = self._hit_stats['overall']
        overall_total = overall['hit'] + overall['miss']
        overall_rate = (overall['hit'] / overall_total * 100) if overall_total > 0 else 0
        
        stats['L1_overall'] = {
            'hit': overall['hit'],
            'miss': overall['miss'],
            'total': overall_total,
            'hit_rate': round(overall_rate, 2),
        }
        
        # L2: 组件缓存
        components = self._hit_stats['components']
        comp_total = components['hit'] + components['miss']
        comp_rate = (components['hit'] / comp_total * 100) if comp_total > 0 else 0
        
        stats['L2_components'] = {
            'hit': components['hit'],
            'miss': components['miss'],
            'total': comp_total,
            'hit_rate': round(comp_rate, 2),
        }
        
        # 总体命中率
        total_hit = overall['hit'] + components['hit']
        total_miss = overall['miss'] + components['miss']
        total_all = total_hit + total_miss
        total_rate = (total_hit / total_all * 100) if total_all > 0 else 0
        
        stats['overall'] = {
            'hit': total_hit,
            'miss': total_miss,
            'total': total_all,
            'hit_rate': round(total_rate, 2),
        }
        
        return stats
    
    def reset_hit_stats(self):
        """重置缓存命中率统计"""
        self._hit_stats = {
            'overall': {'hit': 0, 'miss': 0},
            'components': {'hit': 0, 'miss': 0},
        }
        logger.info("[DashboardCache] 缓存命中率统计已重置")
    
    # =========================================================================
    # 5. 后台预热任务
    # =========================================================================
    
    async def start_warmup_scheduler(self, user_id_fetcher, data_fetcher):
        """
        启动后台缓存预热任务
        
        Args:
            user_id_fetcher: 获取所有需要预热的用户ID async () -> List[int]
            data_fetcher: 数据获取函数 async (user_id) -> dashboard_data
        """
        if self._warmup_task is not None:
            logger.warning("[DashboardCache] 预热任务已在运行")
            return
        
        async def warmup_loop():
            """后台预热循环"""
            while True:
                try:
                    # 获取所有用户ID
                    user_ids = await user_id_fetcher()
                    
                    if user_ids:
                        logger.info(f"[DashboardCache] 后台预热启动，用户数={len(user_ids)}")
                        await self.warmup_all_users_cache(user_ids, data_fetcher)
                    
                    # 等待下次预热
                    await asyncio.sleep(WARMUP_CONFIG['interval'])
                except asyncio.CancelledError:
                    logger.info("[DashboardCache] 预热任务被取消")
                    break
                except Exception as e:
                    logger.error(f"[DashboardCache] 预热任务异常: {e}")
                    await asyncio.sleep(WARMUP_CONFIG['interval'])
        
        self._warmup_task = asyncio.create_task(warmup_loop())
        logger.info("[DashboardCache] 后台预热任务已启动")
    
    async def stop_warmup_scheduler(self):
        """停止后台缓存预热任务"""
        if self._warmup_task is not None:
            self._warmup_task.cancel()
            try:
                await self._warmup_task
            except asyncio.CancelledError:
                pass
            self._warmup_task = None
            logger.info("[DashboardCache] 后台预热任务已停止")


# 全局实例（在main.py中初始化）
dashboard_cache_manager = None


def get_dashboard_cache_manager(redis_cache) -> DashboardCacheManager:
    """
    获取Dashboard缓存管理器单例
    
    Args:
        redis_cache: Redis缓存实例
        
    Returns:
        DashboardCacheManager实例
    """
    global dashboard_cache_manager
    if dashboard_cache_manager is None:
        dashboard_cache_manager = DashboardCacheManager(redis_cache)
    return dashboard_cache_manager
