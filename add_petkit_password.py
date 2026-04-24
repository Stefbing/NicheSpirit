"""添加 PetKit password 配置"""
from backend.app.models.db import get_session, init_db
from backend.app.utils.config_manager import ConfigManager, ConfigEncryptor

init_db()
config_mgr = ConfigManager(ConfigEncryptor())

with get_session() as session:
    # 检查是否已有 password
    from sqlmodel import select
    from backend.app.models.models import SystemConfig
    
    existing = session.exec(
        select(SystemConfig).where(
            SystemConfig.user_id == 1,
            SystemConfig.key == "password",
            SystemConfig.platform == "petkit"
        )
    ).first()
    
    if not existing:
        config_mgr.upsert_config(
            session,
            user_id=1,
            key="password",
            value="demo-account",
            platform="petkit",
            device_name="主猫厕所",
            is_encrypted=True,
        )
        session.commit()
        print("✓ 已添加 PetKit password 配置")
    else:
        print("ℹ PetKit password 配置已存在")
