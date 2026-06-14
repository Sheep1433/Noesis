import path from 'path'
import vue from '@vitejs/plugin-vue'
import vueJsx from '@vitejs/plugin-vue-jsx'
import UnoCSS from 'unocss/vite'
import AutoImport from 'unplugin-auto-import/vite'

import IconsResolver from 'unplugin-icons/resolver'

import Icons from 'unplugin-icons/vite'
import { NaiveUiResolver } from 'unplugin-vue-components/resolvers'
import Components from 'unplugin-vue-components/vite'
import { defineConfig } from 'vite'

import raw from 'vite-raw-plugin'

export default defineConfig(({ mode }) => {
  const devProxy = {
    // REST/SSE 经 /api 转发；勿开 ws，否则部分环境下大文件 multipart 会与 WS 升级逻辑冲突
    '/api': {
      target: 'http://127.0.0.1:8089',
      changeOrigin: true,
      ws: false,
      timeout: 600_000,
    },
  }

  return {
    base: process.env.VITE_ROUTER_MODE === 'hash' ? '' : '/',
    assetsInclude: ['**/*.png'],
    server: {
      port: 2048,
      strictPort: true,
      host: '127.0.0.1',
      cors: true,
      // HMR 与页面同源，避免 localhost / 127.0.0.1 混用导致 WS 反复失败
      hmr: {
        host: '127.0.0.1',
        port: 2048,
        protocol: 'ws',
      },
      // auto-import 生成的 dts/eslintrc 变更会触发无意义的全量 HMR，进而刷 WS 报错
      watch: {
        ignored: ['**/auto-imports.d.ts', '**/.eslintrc-auto-import.json'],
      },
      proxy: devProxy,
    },
    preview: {
      port: Number(process.env.FRONTEND_PREVIEW_PORT) || 4173,
      strictPort: true,
      host: '0.0.0.0',
      // prod 裸机 preview 仅需 /api 反代后端
      proxy: {
        '/api': devProxy['/api'],
      },
    },
    plugins: [
      UnoCSS(),
      vue(),
      raw({
        fileRegex: /\.md$/,
      }),
      vueJsx(),
      AutoImport({
        include: [/\.[tj]sx?$/, /\.vue\??/],
        imports: [
          'vue',
          'vue-router',
          '@vueuse/core',
          {
            'vue': ['createVNode', 'render'],
            'vue-router': [
              'createRouter',
              'createWebHistory',
              'useRouter',
              'useRoute',
            ],
            'uuid': [['v4', 'uuidv4']],
            'lodash-es': [['*', '_']],
            'naive-ui': [
              'useDialog',
              'useMessage',
              'useNotification',
              'useLoadingBar',
            ],
          },
          {
            from: 'vue',
            imports: [
              'App',
              'VNode',
              'ComponentInternalInstance',
              'GlobalComponents',
              'SetupContext',
              'PropType',
            ],
            type: true,
          },
          {
            from: 'vue-router',
            imports: ['RouteRecordRaw', 'RouteLocationRaw'],
            type: true,
          },
        ],
        resolvers: mode === 'development' ? [] : [NaiveUiResolver()],
        dirs: [
          './src/hooks',
          './src/store/business',
          './src/store/hooks/**',
        ],
        dts: './auto-imports.d.ts',
        eslintrc: {
          // 首次生成后关闭，避免 dev 期间反复写文件触发 HMR 风暴
          enabled: false,
        },
        vueTemplate: true,
      }),
      Components({
        directoryAsNamespace: true,
        collapseSamePrefixes: true,
        resolvers: [
          IconsResolver({
            prefix: 'auto-icon',
          }),
          NaiveUiResolver(),
        ],
      }),
      // Auto use Iconify icon
      Icons({
        autoInstall: true,
        compiler: 'vue3',
        scale: 1.2,
        defaultStyle: '',
        defaultClass: 'unplugin-icon',
        jsx: 'react',
      }),
    ],
    resolve: {
      extensions: [
        '.mjs',
        '.js',
        '.ts',
        '.jsx',
        '.tsx',
        '.json',
        '.less',
        '.css',
      ],
      alias: [
        {
          find: '@',
          replacement: path.resolve(__dirname, 'src'),
        },
      ],
    },
    define: {
      'process.env': process.env,
    },
    css: {
      preprocessorOptions: {
        scss: {
          additionalData: `@use '@/styles/naive-variables.scss' as *;`,
        },
      },
    },
  }
})
