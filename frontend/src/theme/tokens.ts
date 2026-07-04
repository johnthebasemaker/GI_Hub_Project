// Single source of truth for the GI brand palette on the React stack.
// Mirrors the legacy Streamlit theme (config.py + ui_components.py) — if a
// color changes there, change it here too.

export const brand = {
  navy: '#003366', // BRAND_BLUE — primary
  navyLight: '#1A4D80', // BRAND_BLUE_LIGHT — interactive / hover
  navyDark: '#001F40', // BRAND_BLUE_DARK — pressed / active
  gold: '#D4AF37', // BRAND_GOLD — accent, primary actions
  goldLight: '#F0D060', // BRAND_GOLD_LIGHT — highlights, hover
  goldDeep: '#B45309', // legacy light-theme accent — gold that passes contrast on white
}

export const dark = {
  bg: '#0A1628', // app background
  surface: '#162038', // card / panel
  surface2: '#1E3050', // elevated / nested card
  border: '#2A4060', // dividers
  text: '#F0F4F8',
  textSecondary: '#C0CCD8',
  textMuted: '#7A8FA0',
}

export const light = {
  bg: '#F8FAFC',
  surface: '#FFFFFF',
  border: '#E5E7EB',
  text: '#1F2937',
  textSecondary: '#374151',
  textMuted: '#6B7280',
}

// Stock-health / status colors (COLOR_OK / COLOR_LOW / COLOR_CRITICAL).
export const status = {
  ok: '#22C55E',
  low: '#F59E0B',
  critical: '#EF4444',
  info: '#4A90D9',
}

// Role accent colors (ROLES map in config.py).
export const roleColors: Record<string, string> = {
  admin: brand.gold,
  logistics: '#0EA5E9',
  hod: '#6366F1',
  warehouse_user: '#10B981',
  supervisor: brand.navyLight,
  store_keeper: dark.textMuted,
}

// CHART_COLORS in config.py — keep chart hues identical across both apps.
export const chartColors = [
  brand.gold,
  brand.navyLight,
  status.ok,
  status.low,
  status.critical,
  '#2E7D8C',
  '#8B3A62',
  '#4A90D9',
]
