import hljs from 'highlight.js'

import bash from 'highlight.js/lib/languages/bash'
import css from 'highlight.js/lib/languages/css'
// 确保引入需要的语言包
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import python from 'highlight.js/lib/languages/python'
import scss from 'highlight.js/lib/languages/scss'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'

// 注册语言
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('css', css)
hljs.registerLanguage('scss', scss)
hljs.registerLanguage('json', json)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('python', python)

function hljsDefineVue() {
  return {
    subLanguage: 'xml',
    contains: [
      hljs.COMMENT('<!--', '-->', {
        relevance: 10,
      }),
      {
        begin: /^(\s*)(<script>)/gm,
        end: /^(\s*)(<\/script>)/gm,
        subLanguage: 'javascript',
        excludeBegin: true,
        excludeEnd: true,
      },
      {
        begin: /^(\s*)(<script lang=["']ts["']>)/gm,
        end: /^(\s*)(<\/script>)/gm,
        subLanguage: 'typescript',
        excludeBegin: true,
        excludeEnd: true,
      },
      {
        begin: /^(\s*)(<style(\sscoped)?>)/gm,
        end: /^(\s*)(<\/style>)/gm,
        subLanguage: 'css',
        excludeBegin: true,
        excludeEnd: true,
      },
      {
        begin: /^(\s*)(<style lang=["'](scss|sass)["'](\sscoped)?>)/gm,
        end: /^(\s*)(<\/style>)/gm,
        subLanguage: 'scss',
        excludeBegin: true,
        excludeEnd: true,
      },
      {
        begin: /^(\s*)(<style lang=["']stylus["'](\sscoped)?>)/gm,
        end: /^(\s*)(<\/style>)/gm,
        subLanguage: 'stylus',
        excludeBegin: true,
        excludeEnd: true,
      },
    ],
  }
}

hljs.registerLanguage('vue', hljsDefineVue)

export default hljs
