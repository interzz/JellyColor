# ╔══════════════════════════════════════════════════════════════════╗
# ║                        🎨 JellyColor                            ║
# ║     Перекраска стикеров/эмодзи + текстовые шаблоны в Hikka     ║
# ╚══════════════════════════════════════════════════════════════════╝
#
# meta developer: @Iklyu
# scope: hikka_only
# scope: hikka_min 1.6.3
# requires: Pillow fonttools

__version__ = (1, 1, 0)

import asyncio
import glob
import gzip
import io
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from telethon.tl import functions, types
from telethon.tl.types import (
    DocumentAttributeSticker,
    DocumentAttributeCustomEmoji,
    InputStickerSetShortName,
    InputStickerSetID,
    InputStickerSetEmpty,
    Message,
    MessageEntityCustomEmoji,
)

from .. import loader, utils


PRESET_COLORS: Dict[str, str] = {
    "🔴 Красный":    "#FF3B30",
    "🟠 Оранжевый":  "#FF9500",
    "🟡 Жёлтый":     "#FFCC00",
    "🟢 Зелёный":    "#34C759",
    "🔵 Синий":      "#007AFF",
    "🟣 Фиолетовый": "#AF52DE",
    "⚫️ Чёрный":     "#1C1C1E",
    "⚪️ Белый":      "#F2F2F7",
    "🩷 Розовый":    "#FF2D55",
    "🩵 Голубой":    "#5AC8FA",
}

PE = {
    "ok":      "5870633910337015697",
    "err":     "5870657884844462243",
    "brush":   "6050679691004612757",
    "pack":    "5778672437122045013",
    "palette": "5870676941614354370",
    "link":    "5769289093221454192",
    "stats":   "5870921681735781843",
    "clock":   "5983150113483134607",
    "sticker": "5886285355279193209",
    "write":   "5870753782874246579",
}

TEMPLATE_SETS = [
    {"title": "♣️ BLACK HOLE",  "short_name": "main_by_emojicreationbot"},
    {"title": "🎨 COLOR",       "short_name": "main2_by_emojimakers_bot"},
    {"title": "⭐ EXCLUSIVE",   "short_name": "main2_by_emojimakers_bot"},
]

TEMPLATE_PLACEHOLDER = "emc"


def pe(emoji: str, eid: str) -> str:
    return '<tg-emoji emoji-id="' + eid + '">' + emoji + '</tg-emoji>'


def hex_to_rgb(hex_color: str):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def tint_image(img: Image.Image, hex_color: str) -> Image.Image:
    r, g, b = hex_to_rgb(hex_color)
    img = img.convert("RGBA")
    data = img.load()
    for y in range(img.height):
        for x in range(img.width):
            ro, go, bo, ao = data[x, y]
            if ao > 0:
                gray = int(0.299 * ro + 0.587 * go + 0.114 * bo)
                data[x, y] = (int(r * gray / 255), int(g * gray / 255), int(b * gray / 255), ao)
    return img


def tint_lottie(lottie_json: dict, hex_color: str) -> dict:
    r, g, b = hex_to_rgb(hex_color)
    nr, ng, nb = r / 255, g / 255, b / 255

    def _walk(obj):
        if isinstance(obj, dict):
            if "c" in obj and isinstance(obj["c"], dict) and "k" in obj["c"]:
                k = obj["c"]["k"]
                if isinstance(k, list) and len(k) >= 3 and isinstance(k[0], (int, float)):
                    gray = 0.299 * k[0] + 0.587 * k[1] + 0.114 * k[2]
                    obj["c"]["k"] = [nr * gray, ng * gray, nb * gray] + (k[3:] or [1.0])
                elif isinstance(k, list):
                    for kf in k:
                        if isinstance(kf, dict) and "s" in kf:
                            s = kf["s"]
                            if isinstance(s, list) and len(s) >= 3:
                                gray = 0.299 * s[0] + 0.587 * s[1] + 0.114 * s[2]
                                kf["s"] = [nr * gray, ng * gray, nb * gray] + (s[3:] or [1.0])
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(lottie_json)
    return lottie_json



# ─── fonttools-based TGS text replacement ─────────────────────────────────────

_FONT_SEARCH = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
    "/usr/local/share/fonts/NotoSans-Bold.ttf",
]

_CACHED_FONT_PATH = "/tmp/jelly_color_font.ttf"

# Google Fonts CDN — NotoSans Bold (open-source, ~300KB)
_FONT_CDN_URL = (
    "https://github.com/googlefonts/noto-fonts/raw/main/"
    "hinted/ttf/NotoSans/NotoSans-Bold.ttf"
)


def _find_font():
    for p in _FONT_SEARCH:
        if os.path.exists(p):
            return p
    for p in glob.glob("/usr/share/fonts/**/*Bold*.ttf", recursive=True):
        return p
    found = glob.glob("/usr/share/fonts/**/*.ttf", recursive=True)
    return found[0] if found else None


def _ensure_font():
    """Возвращает путь к TTF-шрифту. Если локальных нет — скачивает с CDN."""
    import logging
    log = logging.getLogger("JellyColor")

    p = _find_font()
    if p:
        return p

    # Есть закэшированный скачанный шрифт?
    if os.path.exists(_CACHED_FONT_PATH) and os.path.getsize(_CACHED_FONT_PATH) > 50000:
        return _CACHED_FONT_PATH

    # Скачиваем с CDN
    log.info(f"_ensure_font: no local font found, downloading from CDN...")
    try:
        import urllib.request
        urllib.request.urlretrieve(_FONT_CDN_URL, _CACHED_FONT_PATH)
        if os.path.exists(_CACHED_FONT_PATH) and os.path.getsize(_CACHED_FONT_PATH) > 50000:
            log.info(f"_ensure_font: font downloaded → {_CACHED_FONT_PATH}")
            return _CACHED_FONT_PATH
        else:
            log.error("_ensure_font: downloaded file too small, likely failed")
    except Exception as e:
        log.error(f"_ensure_font: download failed: {e}")
    return None


def _collect_path_verts(obj):
    """Собирает все вершины из sh-путей внутри obj."""
    verts = []
    def _walk(o):
        if isinstance(o, dict):
            if o.get("ty") == "sh":
                k = o.get("ks", {}).get("k", {})
                if isinstance(k, list) and k and isinstance(k[0], dict):
                    k = k[0].get("s", k[0])
                if isinstance(k, dict):
                    for v in k.get("v", []):
                        if isinstance(v, (list, tuple)) and len(v) >= 2:
                            verts.append((float(v[0]), float(v[1])))
            for val in o.values():
                _walk(val)
        elif isinstance(o, list):
            for item in o:
                _walk(item)
    _walk(obj)
    return verts


def _verts_to_bounds(verts):
    if not verts:
        return None
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    return (min(xs), min(ys), max(xs), max(ys))


def _get_textgroup_bounds(lottie):
    """Ищет текстовые пути в Lottie JSON, возвращает (x1,y1,x2,y2).

    Поддерживаемые структуры:
    1. GROUP с nm='TextGroup' (оригинальная структура)
    2. SHAPE LAYER с nm='Text Shape' — пути лежат напрямую в shapes[]
    3. SHAPE LAYER с nm='mylogo' / любым другим — ищем группу с nm содержащим 'text'
    """

    # Проход 1: группа TextGroup (глубокий поиск)
    def find_named_group(obj):
        if isinstance(obj, dict):
            if obj.get("ty") == "gr" and obj.get("nm") == "TextGroup":
                b = _verts_to_bounds(_collect_path_verts(obj))
                if b:
                    return b
            for v in obj.values():
                r = find_named_group(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for item in obj:
                r = find_named_group(item)
                if r:
                    return r
        return None

    b = find_named_group(lottie)
    if b:
        return b

    # Проход 2: слой ty=4 с именем 'Text Shape' — пути напрямую в shapes
    def find_text_shape_layer(layers):
        for layer in layers:
            if layer.get("ty") == 4:
                nm = layer.get("nm", "")
                if "text" in nm.lower() or "Text" in nm:
                    shapes = layer.get("shapes", [])
                    n_sh = sum(1 for s in shapes if s.get("ty") == "sh")
                    has_fl = any(s.get("ty") == "fl" for s in shapes)
                    if n_sh >= 2 and has_fl:
                        b = _verts_to_bounds(_collect_path_verts({"shapes": shapes}))
                        if b:
                            return b
        return None

    # ищем во всех layers: top-level + в assets
    all_layer_lists = [lottie.get("layers", [])]
    for asset in lottie.get("assets", []):
        all_layer_lists.append(asset.get("layers", []))

    for layers in all_layer_lists:
        b = find_text_shape_layer(layers)
        if b:
            return b

    # Проход 4: любая GROUP в любом слое, у которой:
    # — fl как прямой дочерний элемент
    # — вложенные sh-пути с text-подобным aspect ratio (ширина > высоты * 1.3)
    # — нет собственных sh детей (= пути в подгруппах, как в COLOR pack)
    # Ищем по всем layers и assets
    def _group_has_direct_fl(gr):
        return any(x.get("ty") == "fl" for x in gr.get("it", []))

    def _count_direct_sh(gr):
        return sum(1 for x in gr.get("it", []) if x.get("ty") == "sh")

    def _count_nested_sh(gr):
        """Рекурсивно считает sh-пути внутри группы."""
        total = 0
        for item in gr.get("it", []):
            if item.get("ty") == "sh":
                total += 1
            elif item.get("ty") == "gr":
                total += _count_nested_sh(item)
        return total

    def find_unnamed_text_group(obj):
        if isinstance(obj, dict):
            if obj.get("ty") == "gr":
                if (_group_has_direct_fl(obj)
                        and _count_direct_sh(obj) == 0   # пути НЕ прямые — они в подгруппах
                        and _count_nested_sh(obj) >= 3): # но глубоко есть >= 3
                    verts = _collect_path_verts(obj)
                    if verts:
                        xs = [v[0] for v in verts]
                        ys = [v[1] for v in verts]
                        w = max(xs) - min(xs)
                        h = max(ys) - min(ys) + 1e-9
                        # text-like: ширина > высоты * 1.3  ИЛИ хотя бы > 0
                        if w > h * 1.3 or w > 0:
                            return _verts_to_bounds(verts)
            for v in obj.values():
                r = find_unnamed_text_group(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for item in obj:
                r = find_unnamed_text_group(item)
                if r:
                    return r
        return None

    b = find_unnamed_text_group(lottie)
    if b:
        return b

    return None


def _text_to_lottie_shapes(text, font_path, cx, cy, height, max_width=None):
    """Генерирует Lottie shape-paths для текста через fonttools.
    cx/cy — центр, height — высота заглавных букв в единицах Lottie.
    max_width — максимальная ширина текста (если задана, масштаб уменьшается по необходимости)."""
    try:
        from fontTools.ttLib import TTFont
        from fontTools.pens.recordingPen import RecordingPen
    except ImportError as _ft_err:
        import logging
        logging.getLogger("JellyColor").error(f"fontTools not installed: {_ft_err}")
        return []

    ft = TTFont(font_path)
    gs = ft.getGlyphSet()
    cm = ft.getBestCmap() or {}
    upm = ft["head"].unitsPerEm

    os2 = ft.get("OS/2")
    cap_h = float(getattr(os2, "sCapHeight", 0) or getattr(os2, "sTypoAscender", upm * 0.72))
    if cap_h <= 0:
        cap_h = upm * 0.72

    sc = height / cap_h          # масштаб: font units → Lottie units

    # Полная ширина текста
    total_adv = 0.0
    glyph_list = []
    for ch in text:
        # Пробуем основной символ, потом Unicode-замены для апострофа и подобных
        gn = cm.get(ord(ch))
        if not gn or gn not in gs:
            # Fallback для частых символов
            fallbacks = {
                ord("'"): [0x2019, 0x02BC, 0x0060],  # ' → ' ` 
                ord("'"): [0x0027, 0x02BC],
                ord("""): [0x0022],
                ord("""): [0x0022],
                ord("–"): [0x002D],
                ord("—"): [0x002D],
            }
            for alt in fallbacks.get(ord(ch), []):
                gn = cm.get(alt)
                if gn and gn in gs:
                    break
            else:
                gn = None
        adv = float(gs[gn].width) if gn and gn in gs else upm * 0.35
        glyph_list.append((gn, adv))
        total_adv += adv

    # Ограничиваем масштаб по ширине если нужно (текст не должен вылезать за бейдж)
    if max_width and total_adv > 0:
        sc_w = max_width / (total_adv * sc) * sc  # sc_w = max_width / total_adv
        sc = min(sc, sc_w * 0.92)  # 8% отступ по краям

    start_x = cx - total_adv * sc / 2.0
    # baseline: cap_h выровнен по центру cy
    base_y  = cy + (cap_h / 2.0) * sc

    shapes = []
    cur_x  = start_x

    for gn, adv in glyph_list:
        if gn is None:
            cur_x += adv * sc
            continue

        pen = RecordingPen()
        gs[gn].draw(pen)

        vs, ii, oo = [], [], []

        def _close():
            if vs:
                shapes.append({
                    "ty": "sh", "nm": "p",
                    "ks": {"a": 0, "k": {
                        "c": True,
                        "v": [list(v) for v in vs],
                        "i": [list(v) for v in ii],
                        "o": [list(v) for v in oo],
                    }},
                })

        prev_x_s = prev_y_s = 0.0

        for op, args in pen.value:
            if op == "moveTo":
                _close(); vs, ii, oo = [], [], []
                fx, fy = args[0]
                lx = fx * sc + cur_x
                ly = base_y - fy * sc
                vs.append([lx, ly]); ii.append([0.0, 0.0]); oo.append([0.0, 0.0])
                prev_x_s, prev_y_s = lx, ly

            elif op == "lineTo":
                fx, fy = args[0]
                lx = fx * sc + cur_x
                ly = base_y - fy * sc
                vs.append([lx, ly]); ii.append([0.0, 0.0]); oo.append([0.0, 0.0])
                prev_x_s, prev_y_s = lx, ly

            elif op == "curveTo":
                (c1x, c1y), (c2x, c2y), (ex, ey) = args
                pvx, pvy = vs[-1]
                oo[-1] = [c1x * sc + cur_x - pvx, base_y - c1y * sc - pvy]
                nvx = ex * sc + cur_x
                nvy = base_y - ey * sc
                vs.append([nvx, nvy])
                ii.append([c2x * sc + cur_x - nvx, base_y - c2y * sc - nvy])
                oo.append([0.0, 0.0])
                prev_x_s, prev_y_s = nvx, nvy

            elif op == "qCurveTo":
                pts = list(args)
                p0x, p0y = vs[-1]
                for qi in range(len(pts) - 1):
                    qcx, qcy = pts[qi]
                    if qi < len(pts) - 2:
                        qex = (pts[qi][0] + pts[qi + 1][0]) / 2.0
                        qey = (pts[qi][1] + pts[qi + 1][1]) / 2.0
                    else:
                        qex, qey = pts[qi + 1]
                    qcs = (qcx * sc + cur_x, base_y - qcy * sc)
                    qes = (qex * sc + cur_x, base_y - qey * sc)
                    c1s = (p0x + 2/3 * (qcs[0] - p0x), p0y + 2/3 * (qcs[1] - p0y))
                    c2s = (qes[0] + 2/3 * (qcs[0] - qes[0]), qes[1] + 2/3 * (qcs[1] - qes[1]))
                    oo[-1] = [c1s[0] - p0x, c1s[1] - p0y]
                    vs.append(list(qes))
                    ii.append([c2s[0] - qes[0], c2s[1] - qes[1]])
                    oo.append([0.0, 0.0])
                    p0x, p0y = qes

            elif op in ("endPath", "closePath"):
                _close(); vs, ii, oo = [], [], []

        _close()
        cur_x += adv * sc

    return shapes


def _replace_textgroup(lottie, new_path_shapes):
    """Находит текстовый контейнер и заменяет path-shapes, сохраняя fill/stroke/transform.

    Поддерживает:
    1. GROUP nm='TextGroup'
    2. SHAPE LAYER nm='Text Shape' — пути напрямую в shapes[]
    3. SHAPE LAYER с >= 3 paths + fill в shapes (fallback)
    4. Unnamed GROUP с fl + nested paths (COLOR pack: буквы в sub-groups)
    """
    def _has_fill(items):
        return any(x.get("ty") == "fl" for x in items)

    def _is_letter_container(item):
        """Группа-буква: gr, внутри которой нет fl/st — только sh и tr."""
        if item.get("ty") != "gr":
            return False
        inner = item.get("it", [])
        return not _has_fill(inner) and not any(x.get("ty") == "st" for x in inner)

    def _patch_list(lst):
        """Убирает sh/el/rc/sr И sub-groups-буквы, вставляет новые пути."""
        style = [x for x in lst
                 if x.get("ty") not in ("sh", "el", "rc", "sr")
                 and not _is_letter_container(x)]
        lst[:] = new_path_shapes + style
        return True

    # Проход 1: GROUP nm='TextGroup'
    def walk_group(obj):
        if isinstance(obj, dict):
            if obj.get("ty") == "gr" and obj.get("nm") == "TextGroup":
                return _patch_list(obj.setdefault("it", []))
            for v in obj.values():
                if walk_group(v):
                    return True
        elif isinstance(obj, list):
            for item in obj:
                if walk_group(item):
                    return True
        return False

    if walk_group(lottie):
        return True

    # Проход 2 + 3: слои с путями напрямую в shapes[]
    def try_patch_layer_shapes(layers):
        for layer in layers:
            if layer.get("ty") != 4:
                continue
            shapes = layer.get("shapes", [])
            nm = layer.get("nm", "")
            n_sh = sum(1 for s in shapes if s.get("ty") == "sh")
            has_fl = any(s.get("ty") == "fl" for s in shapes)
            is_text_layer = ("text" in nm.lower() or "Text" in nm)
            if (is_text_layer and n_sh >= 2 and has_fl) or (n_sh >= 3 and has_fl):
                return _patch_list(shapes)
        return False

    all_layer_lists = [lottie.get("layers", [])]
    for asset in lottie.get("assets", []):
        all_layer_lists.append(asset.get("layers", []))

    for layers in all_layer_lists:
        if try_patch_layer_shapes(layers):
            return True

    # Проход 4: unnamed GROUP с fl + nested paths (COLOR pack)
    def walk_unnamed(obj):
        if isinstance(obj, dict):
            if obj.get("ty") == "gr":
                items = obj.get("it", [])
                has_fl = _has_fill(items)
                n_direct_sh = sum(1 for x in items if x.get("ty") == "sh")
                has_letter_groups = any(_is_letter_container(x) for x in items)
                if has_fl and (has_letter_groups or n_direct_sh == 0):
                    # считаем вложенные пути
                    def count_nested(it):
                        n = 0
                        for x in it:
                            if x.get("ty") == "sh":
                                n += 1
                            elif x.get("ty") == "gr":
                                n += count_nested(x.get("it", []))
                        return n
                    if count_nested(items) >= 3:
                        return _patch_list(items)
            for v in obj.values():
                if walk_unnamed(v):
                    return True
        elif isinstance(obj, list):
            for item in obj:
                if walk_unnamed(item):
                    return True
        return False

    return walk_unnamed(lottie)


def _find_username_bounds(lottie):
    """Ищет GROUP с nm='USERNAME' → возвращает (bounds, container_list, group_obj)."""
    def walk(obj):
        if isinstance(obj, dict):
            if obj.get("ty") == "gr" and obj.get("nm") == "USERNAME":
                verts = _collect_path_verts(obj)
                b = _verts_to_bounds(verts)
                if b:
                    return b, obj
            for v in obj.values():
                r = walk(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for item in obj:
                r = walk(item)
                if r:
                    return r
        return None
    return walk(lottie)


def _replace_username(lottie, new_text, font_path):
    """Находит GROUP nm='USERNAME', заменяет пути на новый текст."""
    import logging
    log = logging.getLogger("JellyColor")

    res = _find_username_bounds(lottie)
    if not res:
        return False
    bounds, grp = res
    x1, y1, x2, y2 = bounds
    cx     = (x1 + x2) / 2.0
    cy     = (y1 + y2) / 2.0
    height = max(abs(y2 - y1), 1.0)
    width  = max(abs(x2 - x1), 1.0)

    new_shapes = _text_to_lottie_shapes(new_text, font_path, cx, cy, height, max_width=width)
    if not new_shapes:
        log.error(f"_replace_username: no shapes generated for {new_text!r}")
        return False

    items = grp.setdefault("it", [])
    # сохраняем fl/st/tr, убираем sh/gr-буквы
    def _has_fill(lst):
        return any(x.get("ty") == "fl" for x in lst)
    def _is_letter_grp(x):
        if x.get("ty") != "gr":
            return False
        inner = x.get("it", [])
        return not _has_fill(inner)
    style = [x for x in items
             if x.get("ty") not in ("sh", "el", "rc", "sr")
             and not _is_letter_grp(x)]
    items[:] = new_shapes + style
    log.info(f"_replace_username: replaced USERNAME with {len(new_shapes)} paths for {new_text!r}")
    return True


OLD_USERNAME = "@emojicreationbot"
NEW_USERNAME = "@freecreateemoji"


def replace_text_in_tgs(tgs_bytes: bytes, old_text: str, new_text: str) -> bytes:
    """Заменяет текст в TGS: находит 'TextGroup' (shape paths),
    генерирует новые bezier-пути через fonttools и вставляет их.
    Также заменяет @emojicreationbot → @freecreateemoji если найден."""
    import logging
    log = logging.getLogger("JellyColor")

    raw    = gzip.decompress(tgs_bytes)
    lottie = json.loads(raw.decode("utf-8"))

    font_path = _ensure_font()
    if font_path is None:
        log.error("replace_text_in_tgs: no TTF font found on system!")
        return tgs_bytes

    changed = False

    # ── Замена основного текста (emc → new_text) ──────────────────────────────
    bounds = _get_textgroup_bounds(lottie)
    if bounds is None:
        log.warning("replace_text_in_tgs: TextGroup NOT FOUND in lottie")
    else:
        x1, y1, x2, y2 = bounds
        cx     = (x1 + x2) / 2.0
        cy     = (y1 + y2) / 2.0
        height = max(abs(y2 - y1), 5.0)
        width  = max(abs(x2 - x1), 5.0)
        log.debug(f"replace_text_in_tgs: bounds=({x1:.1f},{y1:.1f},{x2:.1f},{y2:.1f}) "
                  f"cx={cx:.1f} cy={cy:.1f} h={height:.1f} w={width:.1f}")

        new_shapes = _text_to_lottie_shapes(new_text, font_path, cx, cy, height, max_width=width)
        if not new_shapes:
            log.error(f"replace_text_in_tgs: 0 shapes for {new_text!r}")
        else:
            replaced = _replace_textgroup(lottie, new_shapes)
            if replaced:
                log.info(f"replace_text_in_tgs: OK — {len(new_shapes)} paths for {new_text!r}")
                changed = True
            else:
                log.error("replace_text_in_tgs: _replace_textgroup failed")

    # ── Замена @emojicreationbot → @freecreateemoji (если есть в стикере) ─────
    if _find_username_bounds(lottie):
        if _replace_username(lottie, NEW_USERNAME, font_path):
            changed = True

    if not changed:
        return tgs_bytes
    return gzip.compress(json.dumps(lottie, separators=(",", ":")).encode("utf-8"))








def _dump_layer(layer, idx, out_lines, depth=0):
    """Рекурсивно дампит Lottie-слой со ВСЕМИ полями."""
    pad = "  " * depth
    ty  = layer.get("ty", "?")
    nm  = layer.get("nm", "")
    mn  = layer.get("mn", "")
    ind = layer.get("ind", "?")
    ref = layer.get("refId", "")

    ty_names = {0: "PRECOMP", 1: "SOLID", 2: "IMAGE", 3: "NULL",
                4: "SHAPE", 5: "TEXT", 6: "AUDIO"}
    ty_label = ty_names.get(ty, f"TYPE{ty}")

    out_lines.append(f"{pad}┌─ LAYER[{idx}]  ty={ty}({ty_label})  ind={ind}  nm={nm!r}  mn={mn!r}  refId={ref!r}")

    # Transform
    ks = layer.get("ks", {})
    def _kval(prop):
        k = prop.get("k", "?")
        a = prop.get("a", 0)
        return f"{'ANIM' if a else 'STATIC'} {k!r}"

    out_lines.append(f"{pad}│  ks.p (position) : {_kval(ks.get('p', {}))}")
    out_lines.append(f"{pad}│  ks.s (scale)    : {_kval(ks.get('s', {}))}")
    out_lines.append(f"{pad}│  ks.r (rotation) : {_kval(ks.get('r', {}))}")
    out_lines.append(f"{pad}│  ks.o (opacity)  : {_kval(ks.get('o', {}))}")
    out_lines.append(f"{pad}│  ks.a (anchor)   : {_kval(ks.get('a', {}))}")
    out_lines.append(f"{pad}│  ip={layer.get('ip','?')}  op={layer.get('op','?')}  st={layer.get('st','?')}  "
                     f"sr={layer.get('sr','?')}  parent={layer.get('parent','—')}")

    # TEXT LAYER
    if ty == 5:
        out_lines.append(f"{pad}│  *** TEXT LAYER ***")
        t_block = layer.get("t", {})
        d_block = t_block.get("d", {})
        k_data  = d_block.get("k", "—")
        out_lines.append(f"{pad}│  t.d.k type : {type(k_data).__name__}")
        out_lines.append(f"{pad}│  t.d.k full : {json.dumps(k_data, ensure_ascii=False)}")
        out_lines.append(f"{pad}│  t.p : {json.dumps(t_block.get('p', {}), ensure_ascii=False)}")
        out_lines.append(f"{pad}│  t.m : {json.dumps(t_block.get('m', {}), ensure_ascii=False)}")
        out_lines.append(f"{pad}│  t.a : {json.dumps(t_block.get('a', []), ensure_ascii=False)}")

    # SHAPE LAYER
    if ty == 4:
        shapes = layer.get("shapes", [])
        out_lines.append(f"{pad}│  shapes count: {len(shapes)}")
        for si, shape in enumerate(shapes):
            _dump_shape(shape, si, out_lines, depth + 1)

    out_lines.append(f"{pad}└{'─' * 60}")


def _dump_shape(shape, idx, out_lines, depth=0):
    """Рекурсивно дампит Lottie shape со ВСЕМИ полями."""
    pad    = "  " * depth
    ty     = shape.get("ty", "?")
    nm     = shape.get("nm", "")
    mn     = shape.get("mn", "")

    ty_names = {
        "gr": "GROUP", "sh": "PATH", "fl": "FILL", "st": "STROKE",
        "gf": "GRAD_FILL", "gs": "GRAD_STROKE", "tr": "TRANSFORM",
        "rc": "RECT", "el": "ELLIPSE", "sr": "POLYSTAR",
        "tm": "TRIM", "rd": "ROUND_CORNERS", "rp": "REPEATER",
        "mm": "MERGE", "pb": "PUCKER_BLOAT", "op": "OFFSET_PATH",
        "zz": "ZIG_ZAG", "tw": "TWIST",
    }
    ty_label = ty_names.get(str(ty), f"?{ty}?")

    out_lines.append(f"{pad}▸ SHAPE[{idx}] ty={ty!r}({ty_label})  nm={nm!r}  mn={mn!r}")

    # GROUP → recurse into items
    if ty == "gr":
        items = shape.get("it", [])
        out_lines.append(f"{pad}  items count: {len(items)}")
        for ii2, item in enumerate(items):
            _dump_shape(item, ii2, out_lines, depth + 1)

    # PATH
    elif ty == "sh":
        ks = shape.get("ks", {})
        a  = ks.get("a", 0)
        k  = ks.get("k", {})
        out_lines.append(f"{pad}  animated: {bool(a)}")
        if a and isinstance(k, list):
            out_lines.append(f"{pad}  keyframes count: {len(k)}")
            for kfi, kf in enumerate(k[:3]):   # first 3 keyframes
                t_val = kf.get("t", "?")
                s_val = kf.get("s", kf)
                if isinstance(s_val, list) and s_val:
                    s_val = s_val[0]
                verts = s_val.get("v", []) if isinstance(s_val, dict) else []
                out_lines.append(f"{pad}  kf[{kfi}] t={t_val}  vertices={len(verts)}")
                if verts:
                    out_lines.append(f"{pad}    v[0..3]: {verts[:4]}")
                    xs = [v[0] for v in verts]; ys = [v[1] for v in verts]
                    out_lines.append(f"{pad}    x_range: [{min(xs):.2f} .. {max(xs):.2f}]  "
                                     f"y_range: [{min(ys):.2f} .. {max(ys):.2f}]")
        else:
            verts = k.get("v", []) if isinstance(k, dict) else []
            closed = k.get("c", False) if isinstance(k, dict) else False
            out_lines.append(f"{pad}  static path  closed={closed}  vertices={len(verts)}")
            if verts:
                out_lines.append(f"{pad}  v[0..5]: {verts[:5]}")
                xs = [v[0] for v in verts]; ys = [v[1] for v in verts]
                out_lines.append(f"{pad}  x_range: [{min(xs):.2f} .. {max(xs):.2f}]  "
                                 f"y_range: [{min(ys):.2f} .. {max(ys):.2f}]")
                out_lines.append(f"{pad}  in_tangents[0..3]:  {k.get('i', [])[:4]}")
                out_lines.append(f"{pad}  out_tangents[0..3]: {k.get('o', [])[:4]}")

    # FILL
    elif ty == "fl":
        c_prop = shape.get("c", {})
        o_prop = shape.get("o", {})
        out_lines.append(f"{pad}  color: {c_prop.get('k', '?')}  opacity: {o_prop.get('k', '?')}")
        out_lines.append(f"{pad}  fillRule(r): {shape.get('r', '?')}")

    # STROKE
    elif ty == "st":
        c_prop = shape.get("c", {})
        o_prop = shape.get("o", {})
        w_prop = shape.get("w", {})
        out_lines.append(f"{pad}  color: {c_prop.get('k', '?')}  opacity: {o_prop.get('k', '?')}  width: {w_prop.get('k', '?')}")

    # GRAD FILL / GRAD STROKE
    elif ty in ("gf", "gs"):
        out_lines.append(f"{pad}  gradient: {json.dumps(shape.get('g', {}), ensure_ascii=False)[:200]}")
        out_lines.append(f"{pad}  sp (start): {shape.get('s', {}).get('k', '?')}")
        out_lines.append(f"{pad}  ep (end):   {shape.get('e', {}).get('k', '?')}")

    # TRANSFORM inside group
    elif ty == "tr":
        for field, label in (("p","pos"),("s","scale"),("r","rot"),("o","opacity"),("a","anchor")):
            prop = shape.get(field, {})
            out_lines.append(f"{pad}  tr.{field}({label}): {prop.get('k', '?')!r}")

    # RECT
    elif ty == "rc":
        out_lines.append(f"{pad}  size: {shape.get('s', {}).get('k', '?')}  "
                         f"pos: {shape.get('p', {}).get('k', '?')}  "
                         f"r: {shape.get('r', {}).get('k', '?')}")

    # ELLIPSE
    elif ty == "el":
        out_lines.append(f"{pad}  size: {shape.get('s', {}).get('k', '?')}  "
                         f"pos: {shape.get('p', {}).get('k', '?')}")

    # TRIM PATHS
    elif ty == "tm":
        out_lines.append(f"{pad}  start: {shape.get('s', {}).get('k', '?')}  "
                         f"end: {shape.get('e', {}).get('k', '?')}  "
                         f"offset: {shape.get('o', {}).get('k', '?')}")


async def recolor_document(client, doc, hex_color: str) -> io.BytesIO:
    data = await client.download_media(doc, bytes)
    mime = getattr(doc, "mime_type", "")
    if mime == "application/x-tgsticker":
        raw = gzip.decompress(data)
        lottie = json.loads(raw)
        lottie = tint_lottie(lottie, hex_color)
        compressed = gzip.compress(json.dumps(lottie).encode())
        buf = io.BytesIO(compressed)
        buf.name = "sticker.tgs"
    else:
        img = Image.open(io.BytesIO(data)).convert("RGBA").resize((512, 512), Image.LANCZOS)
        img = tint_image(img, hex_color)
        buf = io.BytesIO()
        img.save(buf, format="WEBP", lossless=True)
        buf.seek(0)
        buf.name = "sticker.webp"
    buf.seek(0)
    return buf


def validate_short_name(name: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9_]{1,64}", name))


async def _upload_sticker_item(client, me_entity, uploaded_file, mime: str, emoji_str: str, is_emoji_pack: bool):
    if is_emoji_pack:
        sticker_attr = types.DocumentAttributeCustomEmoji(
            alt=emoji_str, stickerset=types.InputStickerSetEmpty(), free=False, text_color=False,
        )
    else:
        sticker_attr = types.DocumentAttributeSticker(
            alt=emoji_str, stickerset=types.InputStickerSetEmpty(),
        )
    if mime == "application/x-tgsticker":
        media = types.InputMediaUploadedDocument(
            file=uploaded_file, mime_type="application/x-tgsticker",
            attributes=[types.DocumentAttributeFilename(file_name="sticker.tgs"), sticker_attr],
        )
    else:
        media = types.InputMediaUploadedDocument(
            file=uploaded_file, mime_type="image/webp",
            attributes=[types.DocumentAttributeFilename(file_name="sticker.webp"), sticker_attr],
        )
    result = await client(functions.messages.UploadMediaRequest(peer=me_entity, media=media))
    real_doc = result.document
    return types.InputStickerSetItem(
        document=types.InputDocument(
            id=real_doc.id, access_hash=real_doc.access_hash, file_reference=real_doc.file_reference,
        ),
        emoji=emoji_str,
    )


@loader.tds
class JellyColorMod(loader.Module):
    """Перекраска стикеров/эмодзи и текстовые шаблоны. .j .jt .tstats"""

    strings = {"name": "JellyColor"}

    def __init__(self):
        self._sessions: Dict[int, Dict[str, Any]] = {}
        self._tsessions: Dict[int, Dict[str, Any]] = {}

    @loader.command()
    async def j(self, message: Message):
        """Ответьте на стикер/премиум эмодзи чтобы начать перекраску"""
        reply = await message.get_reply_message()
        if not reply:
            await utils.answer(message, pe("❌", PE["err"]) + " Ответьте на стикер или премиум эмодзи.")
            return

        target_doc = None
        target_type = None
        target_set_id = None

        if reply.sticker:
            doc = reply.sticker
            for attr in doc.attributes:
                if isinstance(attr, DocumentAttributeSticker):
                    ss = attr.stickerset
                    if isinstance(ss, (InputStickerSetShortName, InputStickerSetID)):
                        target_doc, target_type, target_set_id = doc, "sticker", ss
                        break

        if not target_doc:
            for ent in (reply.entities or []):
                if isinstance(ent, MessageEntityCustomEmoji):
                    emoji_docs = await self._client(
                        functions.messages.GetCustomEmojiDocumentsRequest(document_id=[ent.document_id])
                    )
                    if not emoji_docs:
                        continue
                    doc = emoji_docs[0]
                    for attr in doc.attributes:
                        if isinstance(attr, (DocumentAttributeCustomEmoji, DocumentAttributeSticker)):
                            ss = getattr(attr, "stickerset", None)
                            if ss and not isinstance(ss, InputStickerSetEmpty):
                                target_doc, target_type, target_set_id = doc, "emoji", ss
                                break
                    if target_doc:
                        break

        if not target_doc:
            await utils.answer(message, pe("❌", PE["err"]) + " Не найден стикер или премиум эмодзи.")
            return

        try:
            full_set = await self._client(functions.messages.GetStickerSetRequest(stickerset=target_set_id, hash=0))
        except Exception as e:
            await utils.answer(message, pe("❌", PE["err"]) + " Не удалось получить стикерпак: " + str(e))
            return

        pack_count = len(full_set.documents)
        short_name = getattr(full_set.set, "short_name", "")
        uid = message.sender_id
        self._sessions[uid] = {
            "type": target_type, "doc": target_doc, "set_id": target_set_id,
            "set_short": short_name, "full_set": full_set, "pack_count": pack_count,
            "scope": None, "color": None, "pack_name": None,
            "step": "scope" if pack_count > 1 else "color",
        }
        await message.delete()
        await self.inline.form(text=self._step_text(uid), reply_markup=self._step_markup(uid), message=message)

    def _step_text(self, uid: int) -> str:
        s = self._sessions[uid]
        step = s["step"]
        if step == "scope":
            return (pe("🖌", PE["brush"]) + " <b>Что перекрасить?</b>\n\n"
                    "В паке <code>" + s["set_short"] + "</code> найдено <b>" + str(s["pack_count"]) + "</b> объектов.")
        if step == "color":
            scope_text = "один объект" if s["scope"] == "one" else "весь пак (" + str(s["pack_count"]) + " шт.)"
            return (pe("🖋", PE["palette"]) + " <b>Выберите цвет</b>\n\n"
                    "Будет перекрашено: <b>" + scope_text + "</b>\n"
                    "Нажмите на готовый цвет или введите HEX-код.")
        if step == "name":
            return (pe("🏷", PE["sticker"]) + " <b>Введите название пака</b>\n\n"
                    "Цвет: <code>" + s["color"] + "</code>\n"
                    "Введите короткое имя (латиница, цифры, _).")
        return pe("⏰", PE["clock"]) + " <b>Идёт перекраска...</b>\n\nПожалуйста, подождите."

    def _step_markup(self, uid: int):
        s = self._sessions[uid]
        step = s["step"]
        if step == "scope":
            return [[
                {"text": "Один стикер", "icon_custom_emoji_id": PE["sticker"], "callback": self._cb_scope_one, "args": (uid,)},
                {"text": "Весь пак",    "icon_custom_emoji_id": PE["pack"],    "callback": self._cb_scope_all, "args": (uid,)},
            ]]
        if step == "color":
            rows = []
            row = []
            for label, hv in PRESET_COLORS.items():
                row.append({"text": label, "callback": self._cb_color, "args": (uid, hv)})
                if len(row) == 2:
                    rows.append(row); row = []
            if row:
                rows.append(row)
            rows.append([{"text": "Настроить на сайте", "icon_custom_emoji_id": PE["link"], "url": "https://get-color.ru/"}])
            rows.append([{"text": "Ввести HEX-код", "icon_custom_emoji_id": PE["palette"],
                          "input": "Введите HEX-код (например #FF3B30)", "handler": self._input_color, "args": (uid,)}])
            return rows
        if step == "name":
            type_label = "стикерпака" if s["type"] == "sticker" else "эмодзи-пака"
            return [[{"text": "Ввести название " + type_label, "icon_custom_emoji_id": PE["palette"],
                      "input": "Введите short_name (a-z, 0-9, _)", "handler": self._input_name, "args": (uid,)}]]
        return []

    async def _cb_scope_one(self, call, uid: int):
        s = self._sessions.get(uid)
        if not s: await call.answer("Сессия устарела.", show_alert=True); return
        s["scope"] = "one"; s["step"] = "color"
        await call.edit(text=self._step_text(uid), reply_markup=self._step_markup(uid))

    async def _cb_scope_all(self, call, uid: int):
        s = self._sessions.get(uid)
        if not s: await call.answer("Сессия устарела.", show_alert=True); return
        s["scope"] = "all"; s["step"] = "color"
        await call.edit(text=self._step_text(uid), reply_markup=self._step_markup(uid))

    async def _cb_color(self, call, uid: int, hex_color: str):
        s = self._sessions.get(uid)
        if not s: await call.answer("Сессия устарела.", show_alert=True); return
        s["color"] = hex_color; s["step"] = "name"
        await call.edit(text=self._step_text(uid), reply_markup=self._step_markup(uid))

    async def _input_color(self, call, value: str, uid: int):
        s = self._sessions.get(uid)
        if not s: await call.answer("Сессия устарела.", show_alert=True); return
        clean = value.strip()
        if not clean.startswith("#"): clean = "#" + clean
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", clean):
            await call.answer("Неверный формат. Пример: #FF3B30", show_alert=True); return
        s["color"] = clean.upper(); s["step"] = "name"
        await call.edit(text=self._step_text(uid), reply_markup=self._step_markup(uid))

    async def _input_name(self, call, value: str, uid: int):
        s = self._sessions.get(uid)
        if not s: await call.answer("Сессия устарела.", show_alert=True); return
        clean = value.strip().lower()
        if not validate_short_name(clean):
            await call.answer("Только a-z, 0-9, _ (1-64 символа).", show_alert=True); return
        me = await self._client.get_me()
        s["pack_name"] = clean + "_by_" + (me.username or "userbot")
        s["step"] = "processing"
        await call.edit(text=self._step_text(uid))
        asyncio.ensure_future(self._do_recolor(call, uid))

    async def _do_recolor(self, call, uid: int):
        s = self._sessions[uid]
        color, pack_name, pack_type = s["color"], s["pack_name"], s["type"]
        docs = [s["doc"]] if (s["scope"] == "one" or s["pack_count"] == 1) else list(s["full_set"].documents)
        total = len(docs)
        me = await self._client.get_me()
        me_entity = await self._client.get_input_entity("me")
        input_stickers = []

        for i, doc in enumerate(docs):
            try:
                buf = await recolor_document(self._client, doc, color)
                uploaded = await self._client.upload_file(buf, file_name=buf.name)
                mime = getattr(doc, "mime_type", "image/webp")
                emoji_str = "🎨"
                for attr in doc.attributes:
                    if isinstance(attr, (DocumentAttributeCustomEmoji, DocumentAttributeSticker)):
                        emoji_str = getattr(attr, "alt", None) or "🎨"; break
                item = await _upload_sticker_item(self._client, me_entity, uploaded, mime, emoji_str, pack_type == "emoji")
                input_stickers.append(item)
            except Exception:
                pass
            if total > 1:
                bar = "█" * (i + 1) + "░" * (total - i - 1)
                pct = int((i + 1) / total * 100)
                await call.edit(text=(
                    pe("⏰", PE["clock"]) + " <b>Перекраска...</b>\n\n"
                    "<code>[" + bar + "]</code> " + str(pct) + "%\n"
                    "Обработано: <b>" + str(i + 1) + "/" + str(total) + "</b>"
                ))
            await asyncio.sleep(0.05)

        try:
            if not input_stickers: raise ValueError("Нет стикеров для загрузки")
            is_emojis = (pack_type == "emoji")
            await self._client(functions.stickers.CreateStickerSetRequest(
                user_id=me.id, title="JellyColor " + color + " Pack",
                short_name=pack_name, stickers=input_stickers, emojis=is_emojis,
            ))
            pack_link = "https://t.me/" + ("addemoji/" if is_emojis else "addstickers/") + pack_name
        except Exception as e:
            await call.edit(text=pe("❌", PE["err"]) + " <b>Ошибка:</b>\n<code>" + str(e) + "</code>"); return

        stats = self.db.get("JellyColor", "stats", [])
        stats.append({"name": pack_name, "link": pack_link, "color": color, "count": total, "type": pack_type})
        self.db.set("JellyColor", "stats", stats)
        type_label = "Стикерпак" if pack_type == "sticker" else "Эмодзи-пак"
        await call.edit(
            text=(pe("✅", PE["ok"]) + " <b>Готово!</b>\n\n"
                  + pe("🖌", PE["brush"]) + " " + type_label + " перекрашен в <code>" + color + "</code>\n"
                  + pe("📦", PE["pack"]) + " Обработано: <b>" + str(total) + "</b>\n\n"
                  + pe("🔗", PE["link"]) + " <a href=\"" + pack_link + "\">" + pack_link + "</a>"),
            reply_markup=[[{"text": "Открыть пак", "icon_custom_emoji_id": PE["link"], "url": pack_link}]],
        )
        self._sessions.pop(uid, None)

    @loader.command()
    async def jt(self, message: Message):
        """Создать эмодзи-пак из шаблона с вашим текстом"""
        uid = message.sender_id
        self._tsessions[uid] = {"step": "template", "template": None, "text": None, "pack_name": None}
        await message.delete()
        await self.inline.form(text=self._jt_text(uid), reply_markup=self._jt_markup(uid), message=message)

    def _jt_text(self, uid: int) -> str:
        s = self._tsessions[uid]
        step = s["step"]
        if step == "template":
            return (pe("🖌", PE["brush"]) + " <b>Выберите шаблон эмодзи-пака</b>\n\n"
                    "Текст <code>" + TEMPLATE_PLACEHOLDER + "</code> на каждом эмодзи будет заменён на ваш.")
        if step == "text":
            return (pe("✍️", PE["write"]) + " <b>Введите ваш текст</b>\n\n"
                    "Шаблон: <b>" + s["template"]["title"] + "</b>\n"
                    "Текст появится вместо <code>" + TEMPLATE_PLACEHOLDER + "</code> на каждом эмодзи.\n"
                    "Рекомендуется: 2-4 символа.")
        if step == "name":
            return (pe("🏷", PE["sticker"]) + " <b>Введите название пака</b>\n\n"
                    "Шаблон: <b>" + s["template"]["title"] + "</b>\n"
                    "Ваш текст: <code>" + s["text"] + "</code>\n"
                    "Только a-z, 0-9, _ (1-64 символа).")
        return pe("⏰", PE["clock"]) + " <b>Создаём эмодзи-пак...</b>\n\nПожалуйста, подождите."

    def _jt_markup(self, uid: int):
        s = self._tsessions[uid]
        step = s["step"]
        if step == "template":
            return [[{"text": tmpl["title"], "icon_custom_emoji_id": PE["sticker"],
                      "callback": self._jt_cb_template, "args": (uid, i)}]
                    for i, tmpl in enumerate(TEMPLATE_SETS)]
        if step == "text":
            return [[{"text": "Ввести текст", "icon_custom_emoji_id": PE["palette"],
                      "input": "Введите текст (вместо " + TEMPLATE_PLACEHOLDER + ")",
                      "handler": self._jt_input_text, "args": (uid,)}]]
        if step == "name":
            return [[{"text": "Ввести название пака", "icon_custom_emoji_id": PE["palette"],
                      "input": "Введите short_name пака (a-z, 0-9, _)",
                      "handler": self._jt_input_name, "args": (uid,)}]]
        return []

    async def _jt_cb_template(self, call, uid: int, idx: int):
        s = self._tsessions.get(uid)
        if not s: await call.answer("Сессия устарела.", show_alert=True); return
        s["template"] = TEMPLATE_SETS[idx]; s["step"] = "text"
        await call.edit(text=self._jt_text(uid), reply_markup=self._jt_markup(uid))

    async def _jt_input_text(self, call, value: str, uid: int):
        s = self._tsessions.get(uid)
        if not s: await call.answer("Сессия устарела.", show_alert=True); return
        clean = value.strip()
        if not clean: await call.answer("Текст не может быть пустым.", show_alert=True); return
        if len(clean) > 12: await call.answer("Максимум 12 символов.", show_alert=True); return
        s["text"] = clean; s["step"] = "name"
        await call.edit(text=self._jt_text(uid), reply_markup=self._jt_markup(uid))

    async def _jt_input_name(self, call, value: str, uid: int):
        s = self._tsessions.get(uid)
        if not s: await call.answer("Сессия устарела.", show_alert=True); return
        clean = value.strip().lower()
        if not validate_short_name(clean):
            await call.answer("Только a-z, 0-9, _ (1-64 символа).", show_alert=True); return
        me = await self._client.get_me()
        s["pack_name"] = clean + "_by_" + (me.username or "userbot")
        s["step"] = "processing"
        await call.edit(text=self._jt_text(uid))
        asyncio.ensure_future(self._jt_do_create(call, uid))

    async def _jt_do_create(self, call, uid: int):
        s = self._tsessions[uid]
        tmpl, user_text, pack_name = s["template"], s["text"], s["pack_name"]
        try:
            full_set = await self._client(functions.messages.GetStickerSetRequest(
                stickerset=types.InputStickerSetShortName(short_name=tmpl["short_name"]), hash=0,
            ))
        except Exception as e:
            await call.edit(text=pe("❌", PE["err"]) + " Не удалось загрузить шаблон: <code>" + str(e) + "</code>"); return

        docs = list(full_set.documents)
        total = len(docs)
        me = await self._client.get_me()
        me_entity = await self._client.get_input_entity("me")
        input_stickers = []

        for i, doc in enumerate(docs):
            try:
                raw = await self._client.download_media(doc, bytes)
                mime = getattr(doc, "mime_type", "")
                if mime == "application/x-tgsticker":
                    patched = replace_text_in_tgs(raw, TEMPLATE_PLACEHOLDER, user_text)
                    buf = io.BytesIO(patched); buf.name = "sticker.tgs"
                else:
                    img = Image.open(io.BytesIO(raw)).convert("RGBA").resize((512, 512), Image.LANCZOS)
                    out = io.BytesIO(); img.save(out, format="WEBP", lossless=True); out.seek(0)
                    buf = out; buf.name = "sticker.webp"
                emoji_str = "✨"
                for attr in doc.attributes:
                    if isinstance(attr, (DocumentAttributeCustomEmoji, DocumentAttributeSticker)):
                        emoji_str = getattr(attr, "alt", None) or "✨"; break
                uploaded = await self._client.upload_file(buf, file_name=buf.name)
                item = await _upload_sticker_item(self._client, me_entity, uploaded, mime, emoji_str, True)
                input_stickers.append(item)
                if total > 1:
                    bar = "█" * (i + 1) + "░" * (total - i - 1)
                    pct = int((i + 1) / total * 100)
                    await call.edit(text=(
                        pe("⏰", PE["clock"]) + " <b>Создаём эмодзи-пак...</b>\n\n"
                        "<code>[" + bar + "]</code> " + str(pct) + "%\n"
                        "Обработано: <b>" + str(i + 1) + "/" + str(total) + "</b>"
                    ))
            except Exception:
                pass
            await asyncio.sleep(0.05)

        if not input_stickers:
            await call.edit(text=pe("❌", PE["err"]) + " Не удалось обработать ни одного эмодзи.")
            self._tsessions.pop(uid, None); return

        try:
            await self._client(functions.stickers.CreateStickerSetRequest(
                user_id=me.id, title=user_text + " Emoji Pack",
                short_name=pack_name, stickers=input_stickers, emojis=True,
            ))
            pack_link = "https://t.me/addemoji/" + pack_name
        except Exception as e:
            await call.edit(text=pe("❌", PE["err"]) + " Ошибка создания пака: <code>" + str(e) + "</code>")
            self._tsessions.pop(uid, None); return

        stats = self.db.get("JellyColor", "stats", [])
        stats.append({"name": pack_name, "link": pack_link, "color": "text", "count": total, "type": "emoji"})
        self.db.set("JellyColor", "stats", stats)
        await call.edit(
            text=(pe("✅", PE["ok"]) + " <b>Готово!</b>\n\n"
                  + pe("✍️", PE["write"]) + " Текст: <code>" + user_text + "</code>\n"
                  + pe("📦", PE["pack"]) + " Эмодзи: <b>" + str(len(input_stickers)) + "</b> шт.\n\n"
                  + pe("🔗", PE["link"]) + " <a href=\"" + pack_link + "\">" + pack_link + "</a>"),
            reply_markup=[[{"text": "Открыть пак", "icon_custom_emoji_id": PE["link"], "url": pack_link}]],
        )
        self._tsessions.pop(uid, None)

    @loader.command()
    async def jdump(self, message: Message):
        """Ответьте на премиум эмодзи — УЛЬТРА-подробный дамп ВСЕГО"""
        reply = await message.get_reply_message()
        if not reply:
            await utils.answer(message, pe("❌", PE["err"]) + " Ответьте на сообщение с премиум эмодзи.")
            return

        target_eid = None
        for ent in (reply.entities or []):
            if isinstance(ent, MessageEntityCustomEmoji):
                target_eid = ent.document_id
                break

        if target_eid is None:
            await utils.answer(message, pe("❌", PE["err"]) + " Не найдено премиум эмодзи.")
            return

        await utils.answer(message, pe("⏰", PE["clock"]) + " Скачиваю и дамплю ВСЁ...")

        docs = await self._client(
            functions.messages.GetCustomEmojiDocumentsRequest(document_id=[target_eid])
        )
        if not docs:
            await utils.answer(message, pe("❌", PE["err"]) + " Нет документа."); return

        doc = docs[0]
        raw_bytes = await self._client.download_media(doc, bytes)
        mime = getattr(doc, "mime_type", "")

        out_lines = []
        W = 80

        def hr(char="=", label=""):
            if label:
                side = (W - len(label) - 2) // 2
                out_lines.append(char * side + " " + label + " " + char * side)
            else:
                out_lines.append(char * W)

        def ln(s=""):
            out_lines.append(str(s))

        def dump_val(v, indent=0):
            pad = "  " * indent
            if isinstance(v, dict):
                for k2, v2 in v.items():
                    if isinstance(v2, (dict, list)):
                        ln(pad + f"{k2}:")
                        dump_val(v2, indent + 1)
                    else:
                        ln(pad + f"{k2}: {v2!r}")
            elif isinstance(v, list):
                for i2, item in enumerate(v):
                    if isinstance(item, (dict, list)):
                        ln(pad + f"[{i2}]:")
                        dump_val(item, indent + 1)
                    else:
                        ln(pad + f"[{i2}]: {item!r}")
            else:
                ln(pad + repr(v))

        # ── HEADER ──────────────────────────────────────────────────────────────
        hr("=", "ULTRA DUMP")
        ln(f"document_id      : {target_eid}")
        ln(f"mime_type        : {mime}")
        ln(f"size (tg)        : {getattr(doc, 'size', '?')} bytes")
        ln(f"raw_bytes_len    : {len(raw_bytes)}")
        ln(f"dc_id            : {getattr(doc, 'dc_id', '?')}")
        ln(f"date             : {getattr(doc, 'date', '?')}")
        ln(f"access_hash      : {getattr(doc, 'access_hash', '?')}")
        ln(f"file_reference   : {getattr(doc, 'file_reference', b'').hex()}")
        ln()

        # ── ATTRIBUTES ──────────────────────────────────────────────────────────
        hr("-", "ATTRIBUTES")
        for ai, attr in enumerate(doc.attributes):
            ln(f"  [{ai}] {type(attr).__name__}")
            for field in ("alt", "stickerset", "free", "text_color", "masks",
                          "mask_coords", "file_name", "w", "h", "duration",
                          "supports_streaming", "waveform", "round_message",
                          "voice", "title", "performer", "loop"):
                val = getattr(attr, field, "___")
                if val != "___":
                    ln(f"      {field}: {val!r}")
        ln()

        # ── TGS/LOTTIE ──────────────────────────────────────────────────────────
        if mime == "application/x-tgsticker":
            hr("=", "TGS → LOTTIE JSON")
            try:
                raw_json = gzip.decompress(raw_bytes)
                ln(f"decompressed_size : {len(raw_json)} bytes")
                lottie = json.loads(raw_json.decode("utf-8"))
            except Exception as e:
                ln(f"DECOMPRESS/PARSE ERROR: {e}")
                ln("RAW HEX (first 512 bytes):")
                ln(raw_bytes[:512].hex())
                lottie = None

            if lottie:
                # ── top-level fields ──
                hr("-", "TOP LEVEL FIELDS")
                for k in ("v", "fr", "ip", "op", "w", "h", "nm", "mn", "ddd"):
                    ln(f"  {k}: {lottie.get(k, '—')!r}")
                ln()

                # ── assets ──
                hr("-", "ASSETS")
                for ai, asset in enumerate(lottie.get("assets", [])):
                    ln(f"  ASSET[{ai}]  id={asset.get('id','')!r}  nm={asset.get('nm','')!r}  "
                       f"mn={asset.get('mn','')!r}  {asset.get('w','?')}x{asset.get('h','?')}  "
                       f"fr={asset.get('fr','?')}")
                    for li, layer in enumerate(asset.get("layers", [])):
                        _dump_layer(layer, li, out_lines, depth=2)
                ln()

                # ── top-level layers ──
                hr("-", "TOP-LEVEL LAYERS")
                for li, layer in enumerate(lottie.get("layers", [])):
                    _dump_layer(layer, li, out_lines, depth=0)
                ln()

                # ── fonts ──
                hr("-", "FONTS")
                fonts = lottie.get("fonts", {})
                ln(json.dumps(fonts, indent=2, ensure_ascii=False))
                ln()

                # ── chars ──
                hr("-", "CHARS")
                chars = lottie.get("chars", [])
                ln(f"chars count: {len(chars)}")
                for c in chars:
                    ln(f"  {c}")
                ln()

                # ── markers ──
                hr("-", "MARKERS")
                for m in lottie.get("markers", []):
                    ln(f"  {m}")
                ln()

                # ── STRING SEARCH: find ALL occurrences of any text-like value ──
                hr("-", "STRING VALUE SEARCH (all string leaves in JSON)")
                str_leaves = []
                def find_strings(obj, path=""):
                    if isinstance(obj, dict):
                        for k2, v2 in obj.items():
                            find_strings(v2, path + "." + str(k2))
                    elif isinstance(obj, list):
                        for i2, item in enumerate(obj):
                            find_strings(item, path + f"[{i2}]")
                    elif isinstance(obj, str) and obj.strip():
                        str_leaves.append((path, obj))
                find_strings(lottie)
                for path, val in str_leaves:
                    ln(f"  {path}  →  {val!r}")
                ln()

                # ── FULL RAW JSON ── (no truncation, separate file)
                hr("=", "FULL RAW JSON")
                full_json = json.dumps(lottie, indent=2, ensure_ascii=False)
                ln(f"total chars: {len(full_json)}")
                ln(full_json)

        elif mime == "image/webp":
            hr("=", "WEBP STATIC IMAGE")
            ln("Текст запечён в пиксели. JSON отсутствует.")
            ln(f"Size: {len(raw_bytes)} bytes")
        elif mime == "video/webm":
            hr("=", "WEBM VIDEO STICKER")
            ln("Видео-стикер. JSON отсутствует.")
        else:
            hr("=", f"UNKNOWN FORMAT: {mime}")
            ln("RAW HEX (first 512):")
            ln(raw_bytes[:512].hex())

        hr("=", "END OF DUMP")

        dump_text = "\n".join(out_lines)

        # отправляем два файла: дамп + сырой TGS
        buf_dump = io.BytesIO(dump_text.encode("utf-8"))
        buf_dump.name = f"dump_{target_eid}.txt"
        buf_dump.seek(0)

        buf_raw = io.BytesIO(raw_bytes)
        buf_raw.name = f"raw_{target_eid}.tgs"
        buf_raw.seek(0)

        await self._client.send_file(
            message.chat_id,
            [buf_dump, buf_raw],
            caption=f"📄 Ultra dump + raw TGS  <code>{target_eid}</code>",
            parse_mode="HTML",
        )



    @loader.command()
    async def tstats(self, message: Message):
        """Статистика перекрасок и созданных паков"""
        stats = self.db.get("JellyColor", "stats", [])
        if not stats:
            await utils.answer(message, pe("📊", PE["stats"]) + " Ещё ни одной операции не было."); return
        lines = [pe("📊", PE["stats"]) + " <b>Статистика</b>\nВсего операций: <b>" + str(len(stats)) + "</b>\n"]
        for i, entry in enumerate(reversed(stats[-20:]), 1):
            t_label = (pe("🏷", PE["sticker"]) + " Стикерпак") if entry.get("type") == "sticker" else (pe("✅", PE["ok"]) + " Эмодзи-пак")
            color_info = "текст" if entry.get("color") == "text" else "<code>" + entry.get("color", "?") + "</code>"
            lines.append(
                "\n<b>" + str(i) + ".</b> " + t_label + " <code>" + entry["name"] + "</code>\n"
                "   " + pe("🖌", PE["brush"]) + " " + color_info + " | "
                + pe("📦", PE["pack"]) + " <b>" + str(entry["count"]) + "</b>\n"
                "   " + pe("🔗", PE["link"]) + " <a href=\"" + entry["link"] + "\">" + entry["link"] + "</a>"
            )
        await utils.answer(message, "\n".join(lines), parse_mode="HTML")