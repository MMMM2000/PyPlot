"""Host-side MLX90640 EEPROM calibration and object-temperature conversion.

This is a small Python port of the Melexis/Adafruit MLX90640 API math used by
the Arduino firmware. The original implementation is Apache-2.0 licensed:
Copyright (C) 2017 Melexis N.V.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

SCALE_ALPHA = 0.000001
PIXELS = 768


def _s16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value > 0x7FFF else value


def _round_c(value: float) -> int:
    return int(value - 0.5) if value < 0 else int(value + 0.5)


@dataclass(frozen=True)
class MLX90640Parameters:
    k_vdd: int
    vdd25: int
    kv_ptat: float
    kt_ptat: float
    v_ptat25: int
    alpha_ptat: float
    gain_ee: int
    tgc: float
    cp_kv: float
    cp_kta: float
    resolution_ee: int
    calibration_mode_ee: int
    ks_ta: float
    ks_to: tuple[float, float, float, float, float]
    ct: tuple[int, int, int, int, int]
    alpha: tuple[int, ...]
    alpha_scale: int
    offset: tuple[int, ...]
    kta: tuple[int, ...]
    kta_scale: int
    kv: tuple[int, ...]
    kv_scale: int
    cp_alpha: tuple[float, float]
    cp_offset: tuple[int, int]
    il_chess_c: tuple[float, float, float]
    broken_pixels: tuple[int, ...]
    outlier_pixels: tuple[int, ...]
    warning: int = 0


class MLX90640Calibration:
    """Convert raw MLX90640 frame RAM into object temperatures in Celsius."""

    def __init__(
        self,
        eeprom_words: Sequence[int],
        *,
        emissivity: float = 0.95,
        reflected_temperature_c: float | None = None,
    ) -> None:
        if len(eeprom_words) != 832:
            raise ValueError("MLX90640 EEPROM dump must contain 832 words")
        self.eeprom_words = tuple(word & 0xFFFF for word in eeprom_words)
        self.params = extract_parameters(self.eeprom_words)
        self.emissivity = emissivity
        self.reflected_temperature_c = reflected_temperature_c

    def ambient_c(self, frame_words: Sequence[int], control: int, status: int) -> float:
        return get_ta(_frame_data(frame_words, control, status), self.params)

    def calculate_to(
        self,
        frame_words: Sequence[int],
        control: int,
        status: int,
    ) -> tuple[float, ...]:
        frame_data = _frame_data(frame_words, control, status)
        ta = get_ta(frame_data, self.params)
        tr = ta - 8.0 if self.reflected_temperature_c is None else self.reflected_temperature_c
        return calculate_to(frame_data, self.params, self.emissivity, tr)


def _frame_data(frame_words: Sequence[int], control: int, status: int) -> list[int]:
    if len(frame_words) != 832:
        raise ValueError("MLX90640 frame RAM must contain 832 words")
    frame_data = [word & 0xFFFF for word in frame_words]
    frame_data.append(control & 0xFFFF)
    frame_data.append(status & 0x0001)
    return frame_data


def extract_parameters(ee: Sequence[int]) -> MLX90640Parameters:
    k_vdd = (ee[51] & 0xFF00) >> 8
    if k_vdd > 127:
        k_vdd -= 256
    k_vdd *= 32
    vdd25 = ee[51] & 0x00FF
    vdd25 = ((vdd25 - 256) << 5) - 8192

    kv_ptat = (ee[50] & 0xFC00) >> 10
    if kv_ptat > 31:
        kv_ptat -= 64
    kv_ptat /= 4096.0
    kt_ptat = ee[50] & 0x03FF
    if kt_ptat > 511:
        kt_ptat -= 1024
    kt_ptat /= 8.0
    v_ptat25 = ee[49]
    alpha_ptat = (ee[16] & 0xF000) / (2**14) + 8.0

    gain_ee = _s16(ee[48])
    tgc = ee[60] & 0x00FF
    if tgc > 127:
        tgc -= 256
    tgc /= 32.0
    resolution_ee = (ee[56] & 0x3000) >> 12
    ks_ta = (ee[60] & 0xFF00) >> 8
    if ks_ta > 127:
        ks_ta -= 256
    ks_ta /= 8192.0

    step = ((ee[63] & 0x3000) >> 12) * 10
    ct = [-40, 0, (ee[63] & 0x00F0) >> 4, (ee[63] & 0x0F00) >> 8, 400]
    ct[2] *= step
    ct[3] = ct[2] + ct[3] * step
    ks_to_scale = 1 << ((ee[63] & 0x000F) + 8)
    ks_to = [
        ee[61] & 0x00FF,
        (ee[61] & 0xFF00) >> 8,
        ee[62] & 0x00FF,
        (ee[62] & 0xFF00) >> 8,
        -0.0002,
    ]
    for index in range(4):
        if ks_to[index] > 127:
            ks_to[index] -= 256
        ks_to[index] /= ks_to_scale

    cp_alpha, cp_offset, cp_kta, cp_kv = _extract_cp(ee)
    alpha, alpha_scale = _extract_alpha(ee, tgc, cp_alpha)
    offset = _extract_offset(ee)
    kta, kta_scale = _extract_kta(ee)
    kv, kv_scale = _extract_kv(ee)
    calibration_mode_ee, il_chess_c = _extract_cilc(ee)
    broken_pixels, outlier_pixels, warning = _extract_deviating_pixels(ee)

    return MLX90640Parameters(
        k_vdd=k_vdd,
        vdd25=vdd25,
        kv_ptat=kv_ptat,
        kt_ptat=kt_ptat,
        v_ptat25=v_ptat25,
        alpha_ptat=alpha_ptat,
        gain_ee=gain_ee,
        tgc=tgc,
        cp_kv=cp_kv,
        cp_kta=cp_kta,
        resolution_ee=resolution_ee,
        calibration_mode_ee=calibration_mode_ee,
        ks_ta=ks_ta,
        ks_to=tuple(ks_to),  # type: ignore[arg-type]
        ct=tuple(ct),  # type: ignore[arg-type]
        alpha=tuple(alpha),
        alpha_scale=alpha_scale,
        offset=tuple(offset),
        kta=tuple(kta),
        kta_scale=kta_scale,
        kv=tuple(kv),
        kv_scale=kv_scale,
        cp_alpha=tuple(cp_alpha),  # type: ignore[arg-type]
        cp_offset=tuple(cp_offset),  # type: ignore[arg-type]
        il_chess_c=tuple(il_chess_c),  # type: ignore[arg-type]
        broken_pixels=tuple(broken_pixels),
        outlier_pixels=tuple(outlier_pixels),
        warning=warning,
    )


def _extract_cp(ee: Sequence[int]) -> tuple[list[float], list[int], float, float]:
    alpha_scale = ((ee[32] & 0xF000) >> 12) + 27
    offset_sp0 = ee[58] & 0x03FF
    if offset_sp0 > 511:
        offset_sp0 -= 1024
    offset_sp1 = (ee[58] & 0xFC00) >> 10
    if offset_sp1 > 31:
        offset_sp1 -= 64
    offset_sp1 += offset_sp0

    alpha_sp0 = ee[57] & 0x03FF
    if alpha_sp0 > 511:
        alpha_sp0 -= 1024
    alpha_sp0 /= 2**alpha_scale
    alpha_sp1 = (ee[57] & 0xFC00) >> 10
    if alpha_sp1 > 31:
        alpha_sp1 -= 64
    alpha_sp1 = (1 + alpha_sp1 / 128.0) * alpha_sp0

    cp_kta = ee[59] & 0x00FF
    if cp_kta > 127:
        cp_kta -= 256
    kta_scale1 = ((ee[56] & 0x00F0) >> 4) + 8
    cp_kta /= 2**kta_scale1

    cp_kv = (ee[59] & 0xFF00) >> 8
    if cp_kv > 127:
        cp_kv -= 256
    kv_scale = (ee[56] & 0x0F00) >> 8
    cp_kv /= 2**kv_scale
    return [alpha_sp0, alpha_sp1], [offset_sp0, offset_sp1], cp_kta, cp_kv


def _extract_alpha(ee: Sequence[int], tgc: float, cp_alpha: Sequence[float]) -> tuple[list[int], int]:
    acc_rem_scale = ee[32] & 0x000F
    acc_column_scale = (ee[32] & 0x00F0) >> 4
    acc_row_scale = (ee[32] & 0x0F00) >> 8
    alpha_scale_ee = ((ee[32] & 0xF000) >> 12) + 30
    alpha_ref = ee[33]

    acc_row = [0] * 24
    for i in range(6):
        p = i * 4
        word = ee[34 + i]
        acc_row[p + 0] = word & 0x000F
        acc_row[p + 1] = (word & 0x00F0) >> 4
        acc_row[p + 2] = (word & 0x0F00) >> 8
        acc_row[p + 3] = (word & 0xF000) >> 12
    acc_row = [value - 16 if value > 7 else value for value in acc_row]

    acc_column = [0] * 32
    for i in range(8):
        p = i * 4
        word = ee[40 + i]
        acc_column[p + 0] = word & 0x000F
        acc_column[p + 1] = (word & 0x00F0) >> 4
        acc_column[p + 2] = (word & 0x0F00) >> 8
        acc_column[p + 3] = (word & 0xF000) >> 12
    acc_column = [value - 16 if value > 7 else value for value in acc_column]

    scratch = [0.0] * PIXELS
    for i in range(24):
        for j in range(32):
            p = 32 * i + j
            value = (ee[64 + p] & 0x03F0) >> 4
            if value > 31:
                value -= 64
            value *= 1 << acc_rem_scale
            value = alpha_ref + (acc_row[i] << acc_row_scale) + (acc_column[j] << acc_column_scale) + value
            value = value / (2**alpha_scale_ee)
            value = value - tgc * (cp_alpha[0] + cp_alpha[1]) / 2.0
            scratch[p] = SCALE_ALPHA / value

    temp = max(scratch)
    alpha_scale = 0
    while temp < 32768:
        temp *= 2
        alpha_scale += 1
    alpha = [_round_c(value * (2**alpha_scale)) for value in scratch]
    return alpha, alpha_scale


def _extract_offset(ee: Sequence[int]) -> list[int]:
    occ_rem_scale = ee[16] & 0x000F
    occ_column_scale = (ee[16] & 0x00F0) >> 4
    occ_row_scale = (ee[16] & 0x0F00) >> 8
    offset_ref = _s16(ee[17])

    occ_row = [0] * 24
    for i in range(6):
        p = i * 4
        word = ee[18 + i]
        occ_row[p + 0] = word & 0x000F
        occ_row[p + 1] = (word & 0x00F0) >> 4
        occ_row[p + 2] = (word & 0x0F00) >> 8
        occ_row[p + 3] = (word & 0xF000) >> 12
    occ_row = [value - 16 if value > 7 else value for value in occ_row]

    occ_column = [0] * 32
    for i in range(8):
        p = i * 4
        word = ee[24 + i]
        occ_column[p + 0] = word & 0x000F
        occ_column[p + 1] = (word & 0x00F0) >> 4
        occ_column[p + 2] = (word & 0x0F00) >> 8
        occ_column[p + 3] = (word & 0xF000) >> 12
    occ_column = [value - 16 if value > 7 else value for value in occ_column]

    offset = [0] * PIXELS
    for i in range(24):
        for j in range(32):
            p = 32 * i + j
            value = (ee[64 + p] & 0xFC00) >> 10
            if value > 31:
                value -= 64
            value *= 1 << occ_rem_scale
            offset[p] = offset_ref + (occ_row[i] << occ_row_scale) + (occ_column[j] << occ_column_scale) + value
    return offset


def _extract_kta(ee: Sequence[int]) -> tuple[list[int], int]:
    kta_rc = [0] * 4
    for index, value in (
        (0, (ee[54] & 0xFF00) >> 8),
        (2, ee[54] & 0x00FF),
        (1, (ee[55] & 0xFF00) >> 8),
        (3, ee[55] & 0x00FF),
    ):
        kta_rc[index] = value - 256 if value > 127 else value

    kta_scale1 = ((ee[56] & 0x00F0) >> 4) + 8
    kta_scale2 = ee[56] & 0x000F
    scratch = [0.0] * PIXELS
    for i in range(24):
        for j in range(32):
            p = 32 * i + j
            split = 2 * (p // 32 - (p // 64) * 2) + p % 2
            value = (ee[64 + p] & 0x000E) >> 1
            if value > 3:
                value -= 8
            value *= 1 << kta_scale2
            scratch[p] = (kta_rc[split] + value) / (2**kta_scale1)

    temp = max(abs(value) for value in scratch)
    scale = 0
    while temp < 64:
        temp *= 2
        scale += 1
    return [_round_c(value * (2**scale)) for value in scratch], scale


def _extract_kv(ee: Sequence[int]) -> tuple[list[int], int]:
    kv_t = [0] * 4
    for index, value in (
        (0, (ee[52] & 0xF000) >> 12),
        (2, (ee[52] & 0x0F00) >> 8),
        (1, (ee[52] & 0x00F0) >> 4),
        (3, ee[52] & 0x000F),
    ):
        kv_t[index] = value - 16 if value > 7 else value
    kv_scale_ee = (ee[56] & 0x0F00) >> 8

    scratch = [0.0] * PIXELS
    for i in range(24):
        for j in range(32):
            p = 32 * i + j
            split = 2 * (p // 32 - (p // 64) * 2) + p % 2
            scratch[p] = kv_t[split] / (2**kv_scale_ee)

    temp = max(abs(value) for value in scratch)
    scale = 0
    while temp < 64:
        temp *= 2
        scale += 1
    return [_round_c(value * (2**scale)) for value in scratch], scale


def _extract_cilc(ee: Sequence[int]) -> tuple[int, list[float]]:
    calibration_mode_ee = ((ee[10] & 0x0800) >> 4) ^ 0x80
    c0 = ee[53] & 0x003F
    if c0 > 31:
        c0 -= 64
    c1 = (ee[53] & 0x07C0) >> 6
    if c1 > 15:
        c1 -= 32
    c2 = (ee[53] & 0xF800) >> 11
    if c2 > 15:
        c2 -= 32
    return calibration_mode_ee, [c0 / 16.0, c1 / 2.0, c2 / 8.0]


def _extract_deviating_pixels(ee: Sequence[int]) -> tuple[list[int], list[int], int]:
    broken: list[int] = []
    outlier: list[int] = []
    for pix in range(PIXELS):
        word = ee[pix + 64]
        if word == 0 and len(broken) < 5:
            broken.append(pix)
        elif (word & 0x0001) != 0 and len(outlier) < 5:
            outlier.append(pix)

    warning = 0
    if len(broken) > 4:
        warning = -3
    elif len(outlier) > 4:
        warning = -4
    elif len(broken) + len(outlier) > 4:
        warning = -5
    else:
        all_bad = broken + outlier
        for i, pix1 in enumerate(all_bad):
            for pix2 in all_bad[i + 1 :]:
                if _adjacent_warning(pix1, pix2):
                    warning = -6
                    break
            if warning:
                break
    return (broken + [0xFFFF] * (5 - len(broken)))[:5], (outlier + [0xFFFF] * (5 - len(outlier)))[:5], warning


def _adjacent_warning(pix1: int, pix2: int) -> bool:
    diff = pix1 - pix2
    return (-34 < diff < -30) or (-2 < diff < 2) or (30 < diff < 34)


def get_vdd(frame_data: Sequence[int], params: MLX90640Parameters) -> float:
    vdd = _s16(frame_data[810])
    resolution_ram = (frame_data[832] & 0x0C00) >> 10
    resolution_correction = (2**params.resolution_ee) / (2**resolution_ram)
    return (resolution_correction * vdd - params.vdd25) / params.k_vdd + 3.3


def get_ta(frame_data: Sequence[int], params: MLX90640Parameters) -> float:
    vdd = get_vdd(frame_data, params)
    ptat = _s16(frame_data[800])
    ptat_art = _s16(frame_data[768])
    ptat_art = (ptat / (ptat * params.alpha_ptat + ptat_art)) * (2**18)
    ta = ptat_art / (1 + params.kv_ptat * (vdd - 3.3)) - params.v_ptat25
    return ta / params.kt_ptat + 25.0


def calculate_to(
    frame_data: Sequence[int],
    params: MLX90640Parameters,
    emissivity: float,
    reflected_temperature_c: float,
) -> tuple[float, ...]:
    subpage = frame_data[833]
    vdd = get_vdd(frame_data, params)
    ta = get_ta(frame_data, params)
    ta4 = (ta + 273.15) ** 4
    tr4 = (reflected_temperature_c + 273.15) ** 4
    ta_tr = tr4 - (tr4 - ta4) / emissivity

    kta_scale = 2**params.kta_scale
    kv_scale = 2**params.kv_scale
    alpha_scale = 2**params.alpha_scale
    alpha_corr_r = [
        1 / (1 + params.ks_to[0] * 40),
        1.0,
        1 + params.ks_to[1] * params.ct[2],
        (1 + params.ks_to[1] * params.ct[2]) * (1 + params.ks_to[2] * (params.ct[3] - params.ct[2])),
    ]

    gain = _s16(frame_data[778])
    if gain == 0:
        raise ValueError("MLX90640 gain word is zero")
    gain = params.gain_ee / gain
    mode = (frame_data[832] & 0x1000) >> 5

    ir_data_cp = [_s16(frame_data[776]) * gain, _s16(frame_data[808]) * gain]
    common_cp = (1 + params.cp_kta * (ta - 25)) * (1 + params.cp_kv * (vdd - 3.3))
    ir_data_cp[0] -= params.cp_offset[0] * common_cp
    if mode == params.calibration_mode_ee:
        ir_data_cp[1] -= params.cp_offset[1] * common_cp
    else:
        ir_data_cp[1] -= (params.cp_offset[1] + params.il_chess_c[0]) * common_cp

    result = [math.nan] * PIXELS
    for pixel in range(PIXELS):
        il_pattern = pixel // 32 - (pixel // 64) * 2
        chess_pattern = il_pattern ^ (pixel - (pixel // 2) * 2)
        conversion_pattern = (
            ((pixel + 2) // 4 - (pixel + 3) // 4 + (pixel + 1) // 4 - pixel // 4)
            * (1 - 2 * il_pattern)
        )
        pattern = il_pattern if mode == 0 else chess_pattern
        if pattern != subpage:
            continue

        ir_data = _s16(frame_data[pixel]) * gain
        kta = params.kta[pixel] / kta_scale
        kv = params.kv[pixel] / kv_scale
        ir_data -= params.offset[pixel] * (1 + kta * (ta - 25)) * (1 + kv * (vdd - 3.3))
        if mode != params.calibration_mode_ee:
            ir_data += params.il_chess_c[2] * (2 * il_pattern - 1) - params.il_chess_c[1] * conversion_pattern
        ir_data -= params.tgc * ir_data_cp[subpage]
        ir_data /= emissivity

        alpha_compensated = SCALE_ALPHA * alpha_scale / params.alpha[pixel]
        alpha_compensated *= 1 + params.ks_ta * (ta - 25)
        sx = alpha_compensated**3 * (ir_data + alpha_compensated * ta_tr)
        sx = _sqrt4(sx) * params.ks_to[1]
        first_denominator = alpha_compensated * (1 - params.ks_to[1] * 273.15) + sx
        to = _sqrt4(ir_data / first_denominator + ta_tr) - 273.15
        if not math.isfinite(to):
            continue

        if to < params.ct[1]:
            temp_range = 0
        elif to < params.ct[2]:
            temp_range = 1
        elif to < params.ct[3]:
            temp_range = 2
        else:
            temp_range = 3

        denominator = alpha_compensated * alpha_corr_r[temp_range] * (
            1 + params.ks_to[temp_range] * (to - params.ct[temp_range])
        )
        result[pixel] = _sqrt4(ir_data / denominator + ta_tr) - 273.15

    return tuple(result)


def _sqrt4(value: float) -> float:
    if value < 0 or not math.isfinite(value):
        return math.nan
    return math.sqrt(math.sqrt(value))
