import { createRequire } from 'node:module'
import path from 'node:path'
import { FileSystemIconLoader } from '@iconify/utils/lib/loader/node-loaders'
import presetRemToPx from '@unocss/preset-rem-to-px'

import {
  defineConfig,
  presetAttributify,
  presetIcons,
  presetWind3,
  transformerAttributifyJsx,
  transformerDirectives,
} from 'unocss'

const require = createRequire(import.meta.url)

/** 显式 require — collections 须为函数，否则 prod 构建无法生成图标 CSS */
const iconCollections = {
  carbon: () => require('@iconify-json/carbon/icons.json'),
  ci: () => require('@iconify-json/ci/icons.json'),
  hugeicons: () => require('@iconify-json/hugeicons/icons.json'),
  ic: () => require('@iconify-json/ic/icons.json'),
  'material-symbols': () => require('@iconify-json/material-symbols/icons.json'),
  mdi: () => require('@iconify-json/mdi/icons.json'),
  mingcute: () => require('@iconify-json/mingcute/icons.json'),
  'svg-spinners': () => require('@iconify-json/svg-spinners/icons.json'),
  uil: () => require('@iconify-json/uil/icons.json'),
}

/** 运行时拼接的 icon class — 无法被静态扫描，须 safelist */
const DYNAMIC_ICON_SAFELIST = [
  'i-hugeicons:search-01',
  'i-hugeicons:settings-01',
  'i-hugeicons:note-edit',
  'i-hugeicons:ai-chat-02',
  'i-hugeicons:add-01',
  'i-hugeicons:voice-id',
  'i-carbon:side-panel-close',
  'i-carbon:side-panel-open',
  'i-carbon:document-blank',
  'i-carbon:document',
  'i-carbon:code',
  'i-carbon:folder',
  'i-carbon:notebook',
  'i-carbon:bot',
  'i-carbon:api',
  'i-carbon:chevron-right',
  'i-carbon:chevron-left',
  'i-carbon:error',
  'i-carbon:checkmark',
  'i-svg-spinners:bars-scale',
  'i-svg-spinners:3-dots-rotate',
  'i-svg-spinners:6-dots-rotate',
  'i-mdi:clipboard-text-outline',
  'i-mdi:file-image-outline',
  'i-material-symbols:file-open-outline',
  'i-ci:copy',
  'i-ic:baseline-check',
]

export default defineConfig({
  presets: [
    presetWind3(),
    presetAttributify(),
    presetIcons({
      collections: {
        'my-svg': FileSystemIconLoader(
          path.join(__dirname, 'src/assets/svg'),
        ),
        ...iconCollections,
      },
      customizations: {
        transform(svg, collection, icon) {
          if (collection === 'my-svg' && icon === 'system-logo') {
            return svg
          }
          return svg
            .replace(/#ffffff/gi, 'currentColor')
            .replace(/#fff\b/gi, 'currentColor')
            .replace(/\bfill="white"/gi, 'fill="currentColor"')
            .replace(/\bstroke="white"/gi, 'stroke="currentColor"')
        },
      },
    }),
    presetRemToPx({
      baseFontSize: 4,
    }),
  ],
  transformers: [
    transformerDirectives(),
    transformerAttributifyJsx(),
  ],
  theme: {
    colors: {
      primary: 'var(--noesis-color-primary)',
      success: 'var(--noesis-color-success)',
      warning: 'var(--noesis-color-warning)',
      danger: 'var(--noesis-color-danger)',
      info: 'var(--noesis-color-info)',
      bgcolor: 'var(--noesis-color-bg)',
      surface: 'var(--noesis-color-bg-elevated)',
      border: 'var(--noesis-color-border)',
      muted: 'var(--noesis-color-text-muted)',
      tab: 'var(--noesis-color-text-tab)',
      qaFault: 'var(--noesis-color-qa-fault)',
      qaTest: 'var(--noesis-color-qa-test)',
    },
  },
  safelist: [
    ...DYNAMIC_ICON_SAFELIST,
    'i-my-svg:chat-index',
    'i-my-svg:chat-knowledge',
    'i-my-svg:chat-skill',
    'i-my-svg:avatar',
    'i-my-svg:user-avatar',
    'i-my-svg:system-logo',
    'i-uil:upload',
    'i-mingcute:send-fill',
    'i-mingcute:arrow-down-fill',
    'i-mingcute:download-2-line',
    'text-primary',
    'text-tab',
    'text-qaFault',
    'text-qaTest',
    'bg-bgcolor',
    'bg-surface',
  ],
  rules: [
    [
      'navbar-shadow', {
        'box-shadow': '0 1px 4px rgb(0 21 41 / 8%)',
      },
    ],
  ],
})
