// craco.config.js
const path = require("path");
// (O require do webpack saiu junto com o EnvironmentPlugin da WEBHOOK_URL; ver configure.)
require("dotenv").config();

// ---------------------------------------------------------------------------
// SEGURANÇA (varredura, itens 12 e 16): build de PRODUÇÃO.
// O `craco build` define NODE_ENV=production antes de ler este arquivo, e o
// webpack do react-scripts lê as variáveis abaixo ao ser carregado, depois.
// Valem para QUALQUER build de produção (Docker, Vercel, `yarn build` na mão),
// não só para a imagem: por isso ficam aqui, e não apenas no Dockerfile.
// ---------------------------------------------------------------------------
const isProductionBuild = process.env.NODE_ENV === "production";
if (isProductionBuild) {
  // Sem source maps: antes o build publicava os arquivos .map, e qualquer
  // visitante lia o código-fonte original (comentários incluídos) no navegador.
  process.env.GENERATE_SOURCEMAP = "false";
  // O react-scripts embute um pequeno script direto no index.html. Um script
  // embutido exigiria liberar 'unsafe-inline' na CSP do nginx (frontend/nginx.conf),
  // o que anularia a principal proteção dela contra XSS. Fica num arquivo próprio.
  process.env.INLINE_RUNTIME_CHUNK = "false";
}


// Environment variable overrides
const config = {
  enableHealthCheck: process.env.ENABLE_HEALTH_CHECK === "true",
};

function makeDevServerV5Compatible(devServerConfig) {
  const {
    https,
    onAfterSetupMiddleware,
    onBeforeSetupMiddleware,
    onListening,
    setupMiddlewares,
    ...compatibleConfig
  } = devServerConfig;

  compatibleConfig.server =
    typeof https === "object"
      ? { type: "https", options: https }
      : https
        ? "https"
        : "http";
  compatibleConfig.headers = {
    ...compatibleConfig.headers,
    "Cross-Origin-Resource-Policy": "same-origin",
  };

  if (onBeforeSetupMiddleware || setupMiddlewares) {
    compatibleConfig.setupMiddlewares = (middlewares, devServer) => {
      if (onBeforeSetupMiddleware) {
        onBeforeSetupMiddleware(devServer);
      }

      return setupMiddlewares
        ? setupMiddlewares(middlewares, devServer)
        : middlewares;
    };
  }

  compatibleConfig.onListening = (devServer) => {
    devServer.close ??= (callback) => devServer.stopCallback(callback);

    if (onListening) {
      onListening(devServer);
    }
    if (onAfterSetupMiddleware) {
      onAfterSetupMiddleware(devServer);
    }
  };

  return compatibleConfig;
}

// Conditionally load health check modules only if enabled
let WebpackHealthPlugin;
let setupHealthEndpoints;
let healthPluginInstance;

if (config.enableHealthCheck) {
  WebpackHealthPlugin = require("./plugins/health-check/webpack-health-plugin");
  setupHealthEndpoints = require("./plugins/health-check/health-endpoints");
  healthPluginInstance = new WebpackHealthPlugin();
}

let webpackConfig = {
  eslint: {
    configure: {
      extends: ["plugin:react-hooks/recommended"],
      rules: {
        "react-hooks/rules-of-hooks": "error",
        "react-hooks/exhaustive-deps": "warn",
      },
    },
  },
  webpack: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
    configure: (webpackConfig, { env }) => {
      // Produto (cadastro pelo paciente): saiu o EnvironmentPlugin da WEBHOOK_URL
      // (túnel ngrok que levava o Google Forms até a API do desenvolvimento). A
      // integração foi removida e nenhum endereço de túnel entra mais no código.
      if (env === "production") {
        // Garantia extra além do GENERATE_SOURCEMAP=false acima.
        webpackConfig.devtool = false;
      }

      // Add ignored patterns to reduce watched directories
        webpackConfig.watchOptions = {
          ...webpackConfig.watchOptions,
          ignored: [
            '**/node_modules/**',
            '**/.git/**',
            '**/build/**',
            '**/dist/**',
            '**/coverage/**',
            '**/public/**',
        ],
      };

      // Add health check plugin to webpack if enabled
      if (config.enableHealthCheck && healthPluginInstance) {
        webpackConfig.plugins.push(healthPluginInstance);
      }
      return webpackConfig;
    },
  },
};

webpackConfig.devServer = (devServerConfig) => {
  // Add health check endpoints if enabled
  if (config.enableHealthCheck && setupHealthEndpoints && healthPluginInstance) {
    const originalSetupMiddlewares = devServerConfig.setupMiddlewares;

    devServerConfig.setupMiddlewares = (middlewares, devServer) => {
      // Call original setup if exists
      if (originalSetupMiddlewares) {
        middlewares = originalSetupMiddlewares(middlewares, devServer);
      }

      // Setup health endpoints
      setupHealthEndpoints(devServer, healthPluginInstance);

      return middlewares;
    };
  }

  return devServerConfig;
};

const configureDevServer = webpackConfig.devServer;
webpackConfig.devServer = (devServerConfig) =>
  makeDevServerV5Compatible(configureDevServer(devServerConfig));

module.exports = webpackConfig;
