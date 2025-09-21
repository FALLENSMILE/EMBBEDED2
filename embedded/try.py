import smbus
import time

bus = smbus.SMBus(1)
address = 0x68

# Power management register
PWR_MGMT_1 = 0x6B
bus.write_byte_data(address, PWR_MGMT_1, 0x00)  # wake up
time.sleep(0.1)

# Accelerometer registers
ACCEL_XOUT_H = 0x3B
ACCEL_YOUT_H = 0x3D
ACCEL_ZOUT_H = 0x3F

def read_word(reg):
    high = bus.read_byte_data(address, reg)
    low = bus.read_byte_data(address, reg+1)
    val = (high << 8) + low
    if val >= 0x8000:
        val = -((65535 - val) + 1)
    return val

def read_accel_g():
    ax = read_word(ACCEL_XOUT_H) / 16384.0  # in g
    ay = read_word(ACCEL_YOUT_H) / 16384.0
    az = read_word(ACCEL_ZOUT_H) / 16384.0
    return (ax, ay, az)

while True:
    ax, ay, az = read_accel_g()
    print(f"Accel X: {ax:.3f} g  Y: {ay:.3f} g  Z: {az:.3f} g")
    time.sleep(0.5)
