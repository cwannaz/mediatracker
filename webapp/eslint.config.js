// Minimal flat ESLint config — the "JS twin" of the Python wiring checks.
// Catches undeclared/unused symbols; expand rules as the app grows.
export default [
  {
    files: ['src/**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: 'module',
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { window: 'readonly', document: 'readonly', setInterval: 'readonly',
                 clearInterval: 'readonly', setTimeout: 'readonly', clearTimeout: 'readonly',
                 WebSocket: 'readonly', console: 'readonly', localStorage: 'readonly',
                 navigator: 'readonly' },
    },
    rules: {
      // Without the React plugin, core no-unused-vars can't see that a
      // capitalized import is used in JSX; ignore those to avoid false positives.
      'no-unused-vars': ['warn', { varsIgnorePattern: '^[A-Z]' }],
      'no-undef': 'error',
    },
  },
]
