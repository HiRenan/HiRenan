# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "fonttools==4.66.1",
#     "uharfbuzz==0.56.2",
# ]
# ///
"""Gera os banners do README (assets/banner-{en,pt}.svg) com os tokens do maib.com.br.

    uv run scripts/build_assets.py          grava os arquivos que mudaram
    uv run scripts/build_assets.py --check  sai com 1 se algum estiver desatualizado

O banner é o card OG do site (MaibPage/app/api/og/route.tsx) em formato de README:
carvão quente, marcas de registro nos cantos, monograma ember, kicker mono, nome em
Geist e régua tracejada. Todo texto vira contorno (path): o GitHub serve SVG com CSP
`default-src 'none'`, então fonte embutida pode não carregar.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import sys
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "scripts" / "fonts"
ASSETS = ROOT / "assets"
BUDGET = 24 * 1024  # bytes por banner

# --- cor ---------------------------------------------------------------------------

# Tokens OKLCH [L, C, H] de MaibPage/app/globals.css. O hex ao lado vem do card OG e
# trava a conversão: se o porte divergir, o build para antes de gravar qualquer coisa.
TOKENS = {
    "carvao": ((0.17, 0.006, 70), "#110f0d"),  # --background
    "lift": ((0.205, 0.007, 70), "#191714"),  # --card, luz tonal da bancada
    "papel": ((0.92, 0.012, 85), "#e8e4dc"),  # --foreground
    "muted": ((0.722, 0.014, 75), "#aaa49c"),  # --muted-foreground
    "borda": ((0.32, 0.008, 72), "#35322e"),  # --border
    "ember": ((0.7, 0.142, 58), "#df8537"),  # --primary, o único sinal
}

TEXT, NON_TEXT = 4.5, 3.0

# (frente, fundo, tipo): text >= 4.5 | ui >= 3.0 | decorative (isento, SC 1.4.11).
# Medido sobre o hex gravado no SVG, que é o que o navegador pinta.
PAIRS = [
    ("papel", "carvao", "text"),
    ("papel", "lift", "text"),
    ("muted", "carvao", "text"),
    ("muted", "lift", "text"),
    ("ember", "carvao", "ui"),
    ("ember", "lift", "ui"),
    ("borda", "carvao", "decorative"),
]

# Âncoras de MaibPage/scripts/check-contrast.mjs (medidas em OKLCH): provam que o
# porte reproduz o verificador do site.
ANCHORS = [("papel", "carvao", 15.1), ("muted", "carvao", 7.76)]


def oklch_to_linear(l: float, c: float, h: float) -> tuple[float, float, float]:
    """OKLCH -> sRGB linear (Ottosson), igual a check-contrast.mjs."""
    a = c * math.cos(math.radians(h))
    b = c * math.sin(math.radians(h))
    lp = l + 0.3963377774 * a + 0.2158037573 * b
    mp = l - 0.1055613458 * a - 0.0638541728 * b
    sp = l - 0.0894841775 * a - 1.291485548 * b
    l3, m3, s3 = lp**3, mp**3, sp**3
    return (
        4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3,
        -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3,
        -0.0041960863 * l3 - 0.7034186147 * m3 + 1.707614701 * s3,
    )


def clamp01(x: float) -> float:
    return min(1.0, max(0.0, x))


def to_hex(oklch: tuple[float, float, float]) -> str:
    def encode(c: float) -> float:
        c = clamp01(c)
        return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055

    return "#" + "".join(f"{round(encode(c) * 255):02x}" for c in oklch_to_linear(*oklch))


def luminance_oklch(oklch: tuple[float, float, float]) -> float:
    r, g, b = oklch_to_linear(*oklch)
    return 0.2126 * clamp01(r) + 0.7152 * clamp01(g) + 0.0722 * clamp01(b)


def luminance_hex(value: str) -> float:
    # Aqui o ponto de partida é o hex já codificado em gama, então o decode sRGB é
    # necessário (o oposto do alerta em check-contrast.mjs, que parte do OKLCH linear).
    def decode(v: int) -> float:
        x = v / 255
        return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4

    r, g, b = (decode(int(value[i : i + 2], 16)) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(la: float, lb: float) -> float:
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def build_palette() -> tuple[dict[str, str], list[str]]:
    """Converte os tokens, confere o porte e roda o portão de contraste."""
    colors: dict[str, str] = {}
    errors: list[str] = []

    print("Tokens OKLCH -> hex:")
    for name, (oklch, expected) in TOKENS.items():
        got = to_hex(oklch)
        ok = got == expected
        if not ok:
            errors.append(f"{name}: {got} != {expected}")
        colors[name] = got
        print(f"  {'ok' if ok else 'XX'}  {name:7} {got}")

    print("Âncoras de check-contrast.mjs:")
    for fg, bg, expected in ANCHORS:
        got = contrast(luminance_oklch(TOKENS[fg][0]), luminance_oklch(TOKENS[bg][0]))
        ok = abs(got - expected) <= max(0.1, expected * 0.02)
        if not ok:
            errors.append(f"âncora {fg}/{bg}: {got:.2f} (esperado ~{expected})")
        print(f"  {'ok' if ok else 'XX'}  {fg}/{bg:<7} {got:5.2f}:1 (esperado ~{expected})")

    print("Contraste no banner:")
    for fg, bg, kind in PAIRS:
        got = contrast(luminance_hex(colors[fg]), luminance_hex(colors[bg]))
        if kind == "decorative":
            status = "isento"
        else:
            limit = TEXT if kind == "text" else NON_TEXT
            status = "PASS" if got >= limit else "FAIL"
            if status == "FAIL":
                errors.append(f"{fg}/{bg}: {got:.2f}:1 < {limit}")
        print(f"  {fg:>5}/{bg:<7} {got:5.2f}:1  {kind:10} {status}")

    return colors, errors


# --- texto -------------------------------------------------------------------------


@dataclass(frozen=True)
class FontSpec:
    file: str
    sha256: str


# Mesmas fontes do card OG (MaibPage@10302e1, app/api/og/fonts). Subset Latin: tem
# acentos, `·` e `—`, mas não tem setas nem `▸`.
SANS = FontSpec("Geist-700.woff", "4cc092a206a6ab3e53be160abbd3e742fd1c089439641f7ad3125496b5ad4280")
MONO = FontSpec("GeistMono-500.woff", "82a52e258430beeca0e179164339114990afc2f8361840db6f2c2209befb44aa")

# Todo texto do banner tem letter-spacing, e o CSS desliga ligaduras opcionais nesse caso.
FEATURES = {"kern": True, "liga": False, "clig": False, "calt": False, "dlig": False}


class Face:
    def __init__(self, spec: FontSpec) -> None:
        data = (FONT_DIR / spec.file).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != spec.sha256:
            raise SystemExit(f"{spec.file}: sha256 {digest} não confere com o fixado")

        # HarfBuzz não abre WOFF: converte para sfnt em memória e usa o mesmo binário
        # para shaping e para os contornos.
        woff = TTFont(BytesIO(data))
        woff.flavor = None
        sfnt = BytesIO()
        woff.save(sfnt)

        self.name = spec.file
        self.tt = TTFont(BytesIO(sfnt.getvalue()))
        self.upm = self.tt["head"].unitsPerEm
        self.cap_height = self.tt["OS/2"].sCapHeight
        self.cmap = self.tt.getBestCmap()
        self.glyphs = self.tt.getGlyphSet()
        self.order = self.tt.getGlyphOrder()
        self.hb = hb.Font(hb.Face(sfnt.getvalue()))
        self.hb.scale = (self.upm, self.upm)


def fmt(v: float) -> str:
    """Uma casa decimal, sem zero à direita e sem `-0`: saída estável entre execuções."""
    s = f"{v:.1f}"
    if s.endswith(".0"):
        s = s[:-2]
    return "0" if s == "-0" else s


@dataclass(frozen=True)
class Line:
    d: str
    left: float  # tinta visível, em unidades do viewBox
    right: float
    top: float


def set_line(
    face: Face,
    text: str,
    *,
    size: float,
    tracking: float,
    x: float,
    baseline: float,
    align: str = "left",
    lang: str = "en",
) -> Line:
    """Compõe uma linha como path, alinhada pela tinta (não pela caixa de avanço)."""
    missing = sorted({ch for ch in text if ord(ch) not in face.cmap})
    if missing:
        codes = ", ".join(f"U+{ord(ch):04X} {ch!r}" for ch in missing)
        raise SystemExit(f"{face.name} não tem glifo para {codes} em {text!r}")

    buf = hb.Buffer()
    buf.add_str(text)
    buf.direction = "ltr"
    buf.script = "Latn"
    buf.language = lang
    hb.shape(face.hb, buf, FEATURES)

    # Posições em unidades da fonte. O tracking entra depois de cada cluster, menos o
    # último, que não conta para a largura.
    placed: list[tuple[str, float, float]] = []
    pen_x = 0.0
    infos, positions = buf.glyph_infos, buf.glyph_positions
    for i, (info, pos) in enumerate(zip(infos, positions)):
        placed.append((face.order[info.codepoint], pen_x + pos.x_offset, pos.y_offset))
        pen_x += pos.x_advance
        if i + 1 < len(infos) and infos[i + 1].cluster != info.cluster:
            pen_x += tracking * face.upm

    bounds = BoundsPen(face.glyphs)
    for name, gx, gy in placed:
        face.glyphs[name].draw(TransformPen(bounds, (1, 0, 0, 1, gx, gy)))
    xmin, ymin, xmax, ymax = bounds.bounds

    scale = size / face.upm
    origin = x - (xmin if align == "left" else xmax) * scale
    pen = SVGPathPen(face.glyphs, ntos=fmt)
    for name, gx, gy in placed:
        matrix = (scale, 0, 0, -scale, origin + gx * scale, baseline - gy * scale)
        face.glyphs[name].draw(TransformPen(pen, matrix))

    return Line(
        d=pen.getCommands(),
        left=origin + xmin * scale,
        right=origin + xmax * scale,
        top=baseline - ymax * scale,
    )


# --- banner ------------------------------------------------------------------------

W, H = 1200, 480
LEFT, RIGHT = 80, 1120  # borda da tinta do conteúdo

NAME = "Renan Mocelin"
WORDMARK = "MAIB"
COPY = {
    "en": {
        "kicker": "AI ENGINEER",
        "footer": ("FLORIANÓPOLIS, BRAZIL", "MAIB.COM.BR"),
        "title": "Renan Mocelin, AI engineer, maib.com.br",
    },
    "pt": {
        "kicker": "ENGENHEIRO DE IA",
        "footer": ("FLORIANÓPOLIS, SC", "MAIB.COM.BR"),
        "title": "Renan Mocelin, engenheiro de IA, maib.com.br",
    },
}

# Uma entrada por jornada (DESIGN.md): o repouso é o padrão e o movimento só existe
# quando o usuário não pediu menos movimento. O nome sobe e o monograma se desenha
# juntos; wordmark e kicker entram 80ms depois; régua e rodapé, 160ms. Grupos
# animados não levam atributo `transform`, que o CSS sobrescreveria.
STYLE = (
    "@media (prefers-reduced-motion:no-preference){"
    ".r{animation:r .48s cubic-bezier(.16,1,.3,1) both}"
    ".r2{animation-delay:.08s}"
    ".r3{animation-delay:.16s}"
    ".m{stroke-dasharray:1 2;animation:m .48s cubic-bezier(.16,1,.3,1) both}"
    "@keyframes r{from{opacity:0;transform:translateY(14px)}}"
    "@keyframes m{from{stroke-dashoffset:1.05}to{stroke-dashoffset:0}}"
    "}"
)

# Path do monograma (app/icon.svg), num quadro de 32 com traço 3.
MONOGRAM = [(8, 24), (8, 8), (16, 17), (24, 8), (24, 24)]
MONOGRAM_SCALE = 1.75


def monogram(center_y: float) -> tuple[str, float]:
    """Monograma com a tinta esquerda em LEFT; devolve o path e a tinta direita."""
    half_stroke = 1.5
    ox = LEFT - (8 - half_stroke) * MONOGRAM_SCALE
    oy = center_y - 16 * MONOGRAM_SCALE
    pts = [(ox + x * MONOGRAM_SCALE, oy + y * MONOGRAM_SCALE) for x, y in MONOGRAM]
    d = "M" + "L".join(f"{fmt(x)} {fmt(y)}" for x, y in pts)
    return d, ox + (24 + half_stroke) * MONOGRAM_SCALE


def banner(lang: str, sans: Face, mono: Face, c: dict[str, str]) -> str:
    copy = COPY[lang]
    mono_cap = mono.cap_height / mono.upm
    sans_cap = sans.cap_height / sans.upm

    # Ritmo vertical: lockup no topo, assinatura (régua + rodapé) embaixo e o bloco
    # kicker + nome centrado no vão entre os dois, como no card OG.
    lockup_center = 80
    lockup_bottom = lockup_center + (16 - 6.5) * MONOGRAM_SCALE  # tinta do monograma
    footer_base = 404
    rule_y = footer_base - 36 * mono_cap - 30
    gap = 34  # base do kicker -> topo das maiúsculas do nome
    block = 36 * mono_cap + gap + 128 * sans_cap
    block_top = (lockup_bottom + rule_y) / 2 - block / 2
    kicker_base = block_top + 36 * mono_cap
    name_base = kicker_base + gap + 128 * sans_cap

    mark_d, mark_right = monogram(lockup_center)
    wordmark = set_line(
        mono, WORDMARK, size=36, tracking=0.3, x=mark_right + 20,
        baseline=lockup_center + 36 * mono_cap / 2, lang=lang,
    )
    square = 16
    kicker = set_line(
        mono, copy["kicker"], size=36, tracking=0.2, x=LEFT + square + 22,
        baseline=kicker_base, lang=lang,
    )
    name = set_line(sans, NAME, size=128, tracking=-0.02, x=LEFT, baseline=name_base, lang=lang)
    foot_l = set_line(
        mono, copy["footer"][0], size=36, tracking=0.2, x=LEFT, baseline=footer_base, lang=lang,
    )
    foot_r = set_line(
        mono, copy["footer"][1], size=36, tracking=0.2, x=RIGHT, baseline=footer_base,
        align="right", lang=lang,
    )
    if name.right > RIGHT or foot_l.right + 48 > foot_r.left:
        raise SystemExit(f"banner-{lang}: texto não cabe entre {LEFT} e {RIGHT}")

    square_y = kicker_base - 36 * mono_cap / 2 - square / 2
    ticks = "M28 54V28H54M1146 28H1172V54M28 426V452H54M1146 452H1172V426"

    # Gradiente radial do OG: radial-gradient(115% 120% at 16% 92%, lift 0%, carvão 58%).
    gradient = (
        '<radialGradient id="bench" gradientUnits="userSpaceOnUse" cx="0" cy="0" r="1" '
        f'gradientTransform="translate({fmt(W * 0.16)} {fmt(H * 0.92)}) '
        f'scale({fmt(W * 1.15)} {fmt(H * 1.2)})">'
        f'<stop offset="0" stop-color="{c["lift"]}"/>'
        f'<stop offset=".58" stop-color="{c["carvao"]}"/>'
        "</radialGradient>"
    )

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img">',
        f"<title>{copy['title']}</title>",
        f"<style>{STYLE}</style>",
        f"<defs>{gradient}</defs>",
        f'<rect width="{W}" height="{H}" rx="6" fill="url(#bench)"/>',
        f'<path d="{ticks}" fill="none" stroke="{c["borda"]}" stroke-width="3"/>',
        f'<path class="m" d="{mark_d}" pathLength="1" fill="none" stroke="{c["ember"]}" '
        'stroke-width="5.25" stroke-linecap="round" stroke-linejoin="round"/>',
        '<g class="r r2">',
        f'<path d="{wordmark.d}" fill="{c["papel"]}"/>',
        f'<rect x="{LEFT}" y="{fmt(square_y)}" width="{square}" height="{square}" '
        f'fill="{c["ember"]}"/>',
        f'<path d="{kicker.d}" fill="{c["muted"]}"/>',
        "</g>",
        f'<path class="r" d="{name.d}" fill="{c["papel"]}"/>',
        '<g class="r r3">',
        f'<path d="M{LEFT} {fmt(rule_y)}H{RIGHT}" stroke="{c["borda"]}" stroke-width="2" '
        'stroke-dasharray="6 6"/>',
        f'<path d="{foot_l.d}" fill="{c["muted"]}"/>',
        f'<path d="{foot_r.d}" fill="{c["muted"]}"/>',
        "</g>",
        "</svg>",
    ]
    return "\n".join(parts) + "\n"


# --- main --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # o console do Windows é cp1252
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Gera os banners do README.")
    parser.add_argument(
        "--check", action="store_true", help="não grava; sai com 1 se algo estiver desatualizado"
    )
    args = parser.parse_args(argv)

    colors, errors = build_palette()
    if errors:
        print("\nRESULTADO: FAIL\n  " + "\n  ".join(errors))
        return 1

    sans, mono = Face(SANS), Face(MONO)
    stale: list[str] = []
    print("Banners:")
    for lang in COPY:
        path = ASSETS / f"banner-{lang}.svg"
        data = banner(lang, sans, mono, colors).encode("utf-8")
        rel = path.relative_to(ROOT).as_posix()
        if len(data) > BUDGET:
            print(f"  XX  {rel}: {len(data) / 1024:.1f} KB passa do orçamento de {BUDGET // 1024} KB")
            return 1
        if path.exists() and path.read_bytes() == data:
            print(f"  ok  {rel} ({len(data) / 1024:.1f} KB, sem mudança)")
            continue
        if args.check:
            stale.append(rel)
            print(f"  XX  {rel} desatualizado")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"  ok  {rel} ({len(data) / 1024:.1f} KB, gravado)")

    if stale:
        print("\nRode `uv run scripts/build_assets.py` e commite: " + ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
