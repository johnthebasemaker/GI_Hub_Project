import { theme } from 'antd'
import type { ThemeConfig } from 'antd'
import { brand, dark, light, status } from './tokens'

// Motion discipline ("subtle-premium"): fast, ease-out, nothing theatrical.
// Phase 21e: the UI face is IBM Plex Sans (self-hosted, index.css); AntD's
// components read the font from this token, not from the page.
const motion = {
  fontFamily: "'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
  motionDurationFast: '0.1s',
  motionDurationMid: '0.15s',
  motionDurationSlow: '0.2s',
}

// Primary actions are gold with navy text in BOTH modes — gold means
// "this matters right now".
const goldButton = {
  colorPrimary: brand.gold,
  colorPrimaryHover: brand.goldLight,
  colorPrimaryActive: '#B8962E',
  primaryColor: brand.navyDark,
  fontWeight: 600,
  primaryShadow: '0 2px 8px rgba(212, 175, 55, 0.28)',
}

export const darkTheme: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: brand.gold,
    colorInfo: status.info,
    colorSuccess: status.ok,
    colorWarning: status.low,
    colorError: status.critical,
    colorLink: brand.goldLight,
    colorBgBase: dark.bg,
    colorBgContainer: dark.surface,
    colorBgElevated: dark.surface2,
    colorBgLayout: 'transparent', // let the body gradient show through
    colorBorder: dark.border,
    colorBorderSecondary: '#22344F',
    colorText: dark.text,
    colorTextSecondary: dark.textSecondary,
    colorTextTertiary: dark.textMuted,
    borderRadius: 8,
    ...motion,
  },
  components: {
    Button: goldButton,
    Layout: {
      headerBg: 'rgba(13, 22, 40, 0.75)', // translucent — pairs with .gi-header blur
      bodyBg: 'transparent',
      siderBg: 'transparent',
    },
    Table: {
      headerBg: '#1B2A47',
      rowHoverBg: 'rgba(26, 77, 128, 0.25)',
    },
    Modal: {
      contentBg: dark.surface2,
      headerBg: dark.surface2,
    },
    Card: {
      headerBg: 'transparent',
    },
  },
}

export const lightTheme: ThemeConfig = {
  token: {
    // Accessible gold for text-level accents (links, tabs, focus) on white —
    // raw #D4AF37 fails contrast on light surfaces.
    colorPrimary: brand.goldDeep,
    colorInfo: brand.navyLight,
    colorSuccess: status.ok,
    colorWarning: status.low,
    colorError: status.critical,
    colorLink: brand.goldDeep,
    colorBgContainer: light.surface,
    colorBgLayout: 'transparent',
    colorBorder: light.border,
    colorBorderSecondary: '#EDF0F4',
    colorText: light.text,
    colorTextSecondary: light.textSecondary,
    colorTextTertiary: light.textMuted,
    borderRadius: 8,
    ...motion,
  },
  components: {
    Button: goldButton, // gold bg + navy text holds up on light too
    Layout: {
      headerBg: 'rgba(255, 255, 255, 0.75)',
      bodyBg: 'transparent',
      siderBg: 'transparent',
    },
    Table: {
      headerBg: '#F1F5F9',
      rowHoverBg: 'rgba(0, 51, 102, 0.05)',
    },
  },
}

// The sider rail is navy in BOTH modes (always-dark brand rail), so its
// subtree gets its own dark-algorithm theme regardless of the app mode.
export const siderTheme: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: brand.gold,
    colorBgBase: dark.bg,
    colorText: dark.textSecondary,
    colorTextSecondary: dark.textMuted,
    ...motion,
  },
  components: {
    Menu: {
      itemBg: 'transparent',
      subMenuItemBg: 'transparent',
      popupBg: dark.surface2,
      groupTitleColor: dark.textMuted,
      itemColor: dark.textSecondary,
      itemHoverColor: dark.text,
      itemHoverBg: 'rgba(26, 77, 128, 0.35)',
      itemSelectedColor: brand.goldLight,
      itemSelectedBg: 'rgba(212, 175, 55, 0.14)',
      itemBorderRadius: 6,
      itemMarginInline: 8,
    },
  },
}

// Phase 15c — Practice wears violet (ruling Q15-3), so it cannot be mistaken
// for Live's navy and gold at a glance; amber stays for the PRACTICE badge.
// Chosen from `CURRENT_ENV` (known synchronously at load, so no Live-coloured
// first paint); what the page SAYS it is still comes from GET /instance, and a
// mismatch is the red banner with every write refused.
const VIOLET = '#8E6CEF'
export const practiceTheme = (t: ThemeConfig): ThemeConfig => ({
  ...t,
  token: { ...t.token, colorPrimary: VIOLET, colorLink: VIOLET, colorInfo: VIOLET },
  components: {
    ...t.components,
    Button: { ...goldButton, colorPrimary: VIOLET, colorPrimaryHover: '#A58BFF',
      colorPrimaryActive: '#7152D6', primaryColor: '#fff', primaryShadow: 'none' },
    Menu: { ...t.components?.Menu, itemSelectedColor: '#CDBDFF',
      itemSelectedBg: 'rgba(142, 108, 239, 0.22)', itemHoverBg: 'rgba(142, 108, 239, 0.16)' },
  },
})
