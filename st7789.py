import machine
import time
import framebuf
import micropython

# 常用颜色定义 (RGB565)
BLACK   = 0x0000
WHITE   = 0xFFFF
RED     = 0xF800
GREEN   = 0x07E0
BLUE    = 0x001F
CYAN    = 0x07FF
YELLOW  = 0xFFE0
GRAY    = 0x8410
MAGENTA = 0xF81F


@micropython.viper
def _fast_draw_matrix(data: ptr8, buf: ptr8, w: int, h: int, ch: int, cl: int, bh: int, bl: int, scale: int):
    byte_idx = 0
    bit_pos = 7
    idx = 0
    
    for row in range(h):
        line_start = idx
        for col in range(w):
            is_set = data[byte_idx] & (1 << bit_pos)
            v_h = ch if is_set else bh
            v_l = cl if is_set else bl
            
            for _ in range(scale):
                buf[idx] = v_h
                buf[idx+1] = v_l
                idx += 2
                
            if bit_pos == 0:
                bit_pos = 7
                byte_idx += 1
            else:
                bit_pos -= 1
                
        line_len = idx - line_start
        for _ in range(scale - 1):
            for i in range(line_len):
                buf[idx] = buf[line_start + i]
                idx += 1


@micropython.viper
def _fill_color_buffer(buf: ptr8, pixels: int, ch: int, cl: int):
    idx = 0
    for _ in range(pixels):
        buf[idx] = ch
        buf[idx + 1] = cl
        idx += 2


@micropython.viper
def _clear_buffer(buf: ptr8, length: int):
    for idx in range(length):
        buf[idx] = 0


# ==========================================
# ST7789V 驱动类 (240x320)
# ==========================================
class ST7789V:
    def __init__(self, spi, cs, dc, rst, width=240, height=320):
        self.spi = spi
        self.cs = machine.Pin(cs, machine.Pin.OUT, value=1)
        self.dc = machine.Pin(dc, machine.Pin.OUT, value=0)
        self.rst = machine.Pin(rst, machine.Pin.OUT, value=1)
        self.width = width
        self.height = height

        self._one = bytearray(1)
        self._window_buf = bytearray(4)
        self._fill_buf = bytearray(4096)
        self._fill_mv = memoryview(self._fill_buf)
        self._fill_color = -1
        self._glyph_buf = bytearray(16 * 16 * 3 * 3 * 2)
        self._glyph_mv = memoryview(self._glyph_buf)
        self._font_buf = bytearray(32)
        
        self.char_buf = bytearray(8)
        self.char_fb = framebuf.FrameBuffer(self.char_buf, 8, 8, framebuf.MONO_HLSB)
        
        self.reset()
        self.init_display()
        self.fill(BLACK)
        
        try:
            self.hzk_file = open('HZK16', 'rb')
        except OSError:
            print("警告: 找不到 HZK16 字库文件，中文将无法显示。")
            self.hzk_file = None

    def write_cmd(self, cmd):
        self._one[0] = cmd
        self.dc.value(0)
        self.cs.value(0)
        try:
            self.spi.write(self._one)
        finally:
            self.cs.value(1)

    def write_data(self, data):
        self.dc.value(1)
        self.cs.value(0)
        try:
            if isinstance(data, int):
                self._one[0] = data
                self.spi.write(self._one)
            else:
                self.spi.write(data)
        finally:
            self.cs.value(1)

    def reset(self):
        self.rst.value(1)
        time.sleep_ms(10)
        self.rst.value(0)
        time.sleep_ms(20)
        self.rst.value(1)
        time.sleep_ms(20)

    # ===== ST7789V 初始化序列 =====
    def init_display(self):
        self.write_cmd(0x01)  # 软件复位
        time.sleep_ms(150)
        
        self.write_cmd(0x11)  # 退出睡眠
        time.sleep_ms(150)
        
        self.write_cmd(0x3A)  # 颜色模式
        self.write_data(0x55)  # 16位 RGB565
        time.sleep_ms(10)
        
        self.write_cmd(0x36)  # 显示方向
        self.write_data(0x60)  # 竖屏正常
        
        self.write_cmd(0xB2)  # 帧率控制
        self.write_data(b'\x0C\x0C\x00\x33\x33')
        
        self.write_cmd(0xB7)  # 门控控制
        self.write_data(0x35)
        
        self.write_cmd(0xBB)  # VCOM 设置
        self.write_data(0x19)
        
        self.write_cmd(0xC0)  # LCM 控制
        self.write_data(0x2C)
        
        self.write_cmd(0xC2)  # VDV 和 VRH
        self.write_data(0x01)
        
        self.write_cmd(0xC3)  # VRH 设置
        self.write_data(0x12)
        
        self.write_cmd(0xC4)  # VDV 设置
        self.write_data(0x20)
        
        self.write_cmd(0xC6)  # 帧率
        self.write_data(0x0F)
        
        self.write_cmd(0xD0)  # 电源控制
        self.write_data(b'\xA4\xA1')
        
        self.write_cmd(0xE0)  # 正电压伽马
        self.write_data(b'\xD0\x04\x0D\x11\x13\x2B\x3F\x54\x4C\x18\x0D\x0B\x1F\x23')
        
        self.write_cmd(0xE1)  # 负电压伽马
        self.write_data(b'\xD0\x04\x0C\x11\x13\x2C\x3F\x44\x51\x2F\x1F\x1F\x20\x23')
        
        self.write_cmd(0x29)  # 开启显示
        time.sleep_ms(20)

    def set_window(self, x, y, w, h):
        self.write_cmd(0x2A)  # 列地址
        x_start = x
        x_end = x + w - 1
        buf = self._window_buf
        buf[0] = (x_start >> 8) & 0xFF
        buf[1] = x_start & 0xFF
        buf[2] = (x_end >> 8) & 0xFF
        buf[3] = x_end & 0xFF
        self.write_data(buf)
        
        self.write_cmd(0x2B)  # 行地址
        y_start = y
        y_end = y + h - 1
        buf[0] = (y_start >> 8) & 0xFF
        buf[1] = y_start & 0xFF
        buf[2] = (y_end >> 8) & 0xFF
        buf[3] = y_end & 0xFF
        self.write_data(buf)
        
        self.write_cmd(0x2C)  # 写内存

    # ===== 以下函数与 ILI9341 完全相同 =====
    def fill_rect(self, x, y, w, h, color):
        x = max(0, min(x, self.width - 1))
        y = max(0, min(y, self.height - 1))
        w = max(0, min(w, self.width - x))
        h = max(0, min(h, self.height - y))
        if w <= 0 or h <= 0:
            return
        self.set_window(x, y, w, h)
        self.dc.value(1)
        self.cs.value(0)
        try:
            ch = color >> 8
            cl = color & 0xFF
            if self._fill_color != color:
                _fill_color_buffer(self._fill_buf, len(self._fill_buf) // 2, ch, cl)
                self._fill_color = color

            remaining_pixels = w * h
            chunk_pixels = len(self._fill_buf) // 2
            while remaining_pixels > 0:
                count = min(chunk_pixels, remaining_pixels)
                self.spi.write(self._fill_mv[:count * 2])
                remaining_pixels -= count
        finally:
            self.cs.value(1)

    def fill(self, color):
        self.fill_rect(0, 0, self.width, self.height, color)

    def _draw_matrix(self, data, w, h, x, y, color, bg_color, scale):
        block_w, block_h = w * scale, h * scale
        if x + block_w > self.width or y + block_h > self.height:
            return
        needed = block_w * block_h * 2
        if needed > len(self._glyph_buf):
            return
        ch, cl = color >> 8, color & 0xFF
        bh, bl = bg_color >> 8, bg_color & 0xFF
        _fast_draw_matrix(data, self._glyph_buf, w, h, ch, cl, bh, bl, scale)
        self.set_window(x, y, block_w, block_h)
        self.dc.value(1)
        self.cs.value(0)
        try:
            self.spi.write(self._glyph_mv[:needed])
        finally:
            self.cs.value(1)

    def draw_gbk(self, gbk_bytes, x, y, color, bg_color=BLACK, scale=1):
        if type(gbk_bytes) == str:
            gbk_bytes = gbk_bytes.encode('gbk')
        curr_x = x
        i = 0
        while i < len(gbk_bytes):
            b1 = gbk_bytes[i]
            if b1 < 0x80:
                self.char_fb.fill(0)
                self.char_fb.text(chr(b1), 0, 0, 1)
                self._draw_matrix(self.char_buf, 8, 8, curr_x, y, color, bg_color, scale)
                curr_x += 8 * scale
                i += 1
            else:
                if i + 1 >= len(gbk_bytes):
                    break
                b2 = gbk_bytes[i+1]
                font_data = self._font_buf
                _clear_buffer(font_data, len(font_data))
                if 0xA1 <= b1 <= 0xF7 and 0xA1 <= b2 <= 0xFE:
                    offset = ((b1 - 0xA1) * 94 + (b2 - 0xA1)) * 32
                    if 0 <= offset <= 267616 - 32:
                        if self.hzk_file:
                            self.hzk_file.seek(offset)
                            try:
                                self.hzk_file.readinto(font_data)
                            except AttributeError:
                                read_data = self.hzk_file.read(32)
                                if len(read_data) == 32:
                                    font_data[:] = read_data
                self._draw_matrix(font_data, 16, 16, curr_x, y, color, bg_color, scale)
                curr_x += 16 * scale
                i += 2

    def draw_gbk_large_ascii(self, gbk_bytes, x, y, color, bg_color=BLACK, scale=1):
        """专门用于机车行显示，英文/数字和中文一样大"""
        if type(gbk_bytes) == str:
            gbk_bytes = gbk_bytes.encode('gbk')
        curr_x = x
        i = 0
        while i < len(gbk_bytes):
            b1 = gbk_bytes[i]
            if b1 < 0x80:
                self.char_fb.fill(0)
                self.char_fb.text(chr(b1), 0, 0, 1)
                self._draw_matrix(self.char_buf, 8, 8, curr_x, y, color, bg_color, scale)
                curr_x += 8 * scale
                i += 1
            else:
                if i + 1 >= len(gbk_bytes):
                    break
                b2 = gbk_bytes[i+1]
                font_data = self._font_buf
                _clear_buffer(font_data, len(font_data))
                if 0xA1 <= b1 <= 0xF7 and 0xA1 <= b2 <= 0xFE:
                    offset = ((b1 - 0xA1) * 94 + (b2 - 0xA1)) * 32
                    if 0 <= offset <= 267616 - 32:
                        if self.hzk_file:
                            self.hzk_file.seek(offset)
                            try:
                                self.hzk_file.readinto(font_data)
                            except AttributeError:
                                read_data = self.hzk_file.read(32)
                                if len(read_data) == 32:
                                    font_data[:] = read_data
                self._draw_matrix(font_data, 16, 16, curr_x, y, color, bg_color, scale)
                curr_x += 16 * scale
                i += 2