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


export default defineConfig({
  presets: [
    presetWind3(),
    presetAttributify(),
    presetIcons({
      customizations: {
        transform(svg, collection, icon) {
          if (collection === 'my-svg' && icon === 'system-logo') {
            return svg
          }
          return svg.replace(/#fff/, 'currentColor')
        },
      },
      collections: {
        'my-svg': FileSystemIconLoader(
          path.join(__dirname, 'src/assets/svg'),
        ),
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
    'i-hugeicons:search-01',
    'i-hugeicons:settings-01',
    'i-hugeicons:note-edit',
    'i-hugeicons:ai-chat-02',
    'i-hugeicons:add-01',
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
