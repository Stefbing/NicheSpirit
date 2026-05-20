/**
 * 小米体脂秤 BLE 广播数据解析器
 * 基于 openScale 和 ble-in-xiaomi 逆向工程
 *
 * Mi Body Composition Scale 2 (XMTZC05HM) 协议:
 * - 13字节 Payload (不含4字节UUID 0xFE95)
 * - 控制字节16位，小端序 (LSB first)
 * - 体重/阻抗均为小端序
 */

// 常量定义
const WEIGHT_SCALE_FACTOR = 200.0; // 小米协议：Raw / 200 = KG
const MIN_WEIGHT_KG = 1.0;
const MAX_WEIGHT_KG = 220.0;
const MIN_IMPEDANCE = 200;
const MAX_IMPEDANCE = 2000;
const REQUIRED_MIN_LENGTH = 13;
const REQUIRED_MAX_LENGTH = 14;

// 控制字节位掩码定义
const CTRL_BIT_LBS = 0x0001;        // Bit 0: 英镑单位
const CTRL_BIT_JIN = 0x0002;        // Bit 1: 斤单位
const CTRL_BIT_WEIGHT_REMOVED = 0x0080;  // Bit 7: 下秤
const CTRL_BIT_IMPEDANCE_VALID = 0x0200; // Bit 9: 阻抗有效
const CTRL_BIT_STABILIZED_ALT = 0x0400;  // Bit 10: 稳定状态（备选）
const CTRL_BIT_STABILIZED = 0x2000;      // Bit 13: 稳定状态

/**
 * 主解析入口
 */
function parse(buffer, macAddress = '') {
    // 小米体脂秤2只需要13-14字节的数据
    if (!buffer || buffer.byteLength < REQUIRED_MIN_LENGTH || buffer.byteLength > REQUIRED_MAX_LENGTH) {
        console.log('[BLE] ⚠️ 数据长度不符合要求:', buffer ? buffer.byteLength : 0, `(需要${REQUIRED_MIN_LENGTH}-${REQUIRED_MAX_LENGTH}字节)`);
        return null;
    }

    try {
        const data = new Uint8Array(buffer);
        const timestamp = Date.now();

        const result = parseFull(data);

        if (result) {
            result.receivedAt = timestamp;
        }
        return result;
    } catch (err) {
        console.error('[BLE] ❌ 解析异常:', err.message || err);
        return null;
    }
}



/**
 * 彻底修正后的 13字节解析 (针对小米体脂秤 2)
 */
function parseFull(data) {
    // 1. 提取控制字节 (小端序)
    const ctrl = (data[1] << 8) | data[0];

    // 2. 解析控制标志位
    const isLbs = (ctrl & CTRL_BIT_LBS) !== 0;
    const isJin = (ctrl & CTRL_BIT_JIN) !== 0;
    const hasImpedance = (ctrl & CTRL_BIT_IMPEDANCE_VALID) !== 0;
    const isStabilized = (ctrl & CTRL_BIT_STABILIZED) !== 0 || (ctrl & CTRL_BIT_STABILIZED_ALT) !== 0;
    const weightRemoved = (ctrl & CTRL_BIT_WEIGHT_REMOVED) !== 0;

    // 3. 解析体重 (Index 11, 12) - 小端序
    const weightRaw = (data[12] << 8) | data[11];
    // 小米协议：Raw 数据除以 200 始终等于 KG（无论秤上显示什么单位）
    const weightKg = weightRaw / WEIGHT_SCALE_FACTOR;

    // 4. 解析阻抗 (Index 9, 10) - 小端序
    let impedance = 0;
    let impedanceValid = false;
    if (hasImpedance) {
        const impedanceRaw = (data[10] << 8) | data[9];
        // 人体阻抗合理范围：200-2000 欧姆
        if (impedanceRaw >= MIN_IMPEDANCE && impedanceRaw <= MAX_IMPEDANCE) {
            impedance = impedanceRaw;
            impedanceValid = true;
        }
    }

    // 5. 过滤无效数据：下秤、体重超出范围
    if (weightRemoved || weightKg <= MIN_WEIGHT_KG || weightKg > MAX_WEIGHT_KG) {
        return null;
    }

    // 6. 解析设备时间（UTC时间）
    const year = (data[3] << 8) | data[2];
    const month = data[4];
    const day = data[5];
    const hour = data[6];
    const minute = data[7];
    const second = data[8];

    // 使用 Date.UTC 生成 UTC 时间戳
    const deviceTimestamp = Date.UTC(year, month - 1, day, hour, minute, second);

    // 7. 返回解析结果
    return {
        weight: Math.round(weightKg * 100) / 100, // 统一输出 KG（保留两位小数）
        impedance,
        impedanceValid,
        isStabilized,
        unit: isLbs ? 'lbs' : (isJin ? 'jin' : 'kg'),
        deviceTimestamp: isNaN(deviceTimestamp) ? null : deviceTimestamp
    };
}

module.exports = {
    parse
};
