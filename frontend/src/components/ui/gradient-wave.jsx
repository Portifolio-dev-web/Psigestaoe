import { memo, useEffect, useRef } from "react";

// ---------------------------------------------------------------------------
// GradientWave: fundo animado de ondas em gradiente (WebGL).
//
// Adaptado de um componente do 21st.dev (original em TypeScript para Next.js)
// para este projeto, que é JavaScript (CRA + shadcn com "tsx": false).
// Mudanças em relação ao original, todas para uso real em produção:
//   1. Sintaxe: o original tinha um `.join("\n")` quebrado em duas linhas
//      (erro de sintaxe) e a diretiva "use client" do Next.js (inútil aqui).
//   2. Recriação a cada render: o useEffect dependia de arrays/objetos
//      criados a cada renderização (cores padrão etc.). Na tela de login,
//      CADA TECLA digitada recriava o contexto WebGL; o navegador derruba
//      contextos depois de ~16 e o fundo piscava. Agora o efeito só roda de
//      novo quando o CONTEÚDO das props muda (chave JSON) e o componente é memo.
//   3. Vazamentos: o listener de `resize` nunca era removido e o contexto da
//      GPU não era liberado ao sair da tela. Agora há destroy().
//   4. Tamanho: o canvas usava o tamanho da JANELA, distorcendo a imagem
//      quando o fundo ocupa só parte da tela (no login, a metade esquerda).
//      Agora acompanha o tamanho do contêiner (ResizeObserver) e PAUSA quando
//      o contêiner está oculto (ex.: painel `hidden` no celular).
//   5. A prop `deform` não tinha efeito: o original trocava os objetos de
//      uniform por números, mas o shader continuava lendo os objetos antigos.
//      Agora o valor de cada uniform é atualizado.
//   6. Shader: o laço lia `u_active_colors[i + 1]` (um vec4) além do índice 3
//      com mais de 4 cores, o que é indefinido no WebGL. Todas as camadas de
//      cor ficam sempre ativas, então a checagem foi removida.
//   7. Acessibilidade: com "reduzir movimento" ativado no sistema, desenha um
//      quadro estático em vez de animar.
// Se o navegador não tiver WebGL, nada é desenhado e aparece o fundo do
// elemento pai (defina uma cor de fundo nele como alternativa).
// ---------------------------------------------------------------------------

function normalizeColor(hexCode) {
  return [((hexCode >> 16) & 255) / 255, ((hexCode >> 8) & 255) / 255, (255 & hexCode) / 255];
}

class MiniGl {
  constructor(canvas) {
    this.canvas = canvas;
    this.meshes = [];
    const gl = this.canvas.getContext("webgl", { antialias: true });
    if (!gl) throw new Error("WebGL not supported");
    this.gl = gl;

    const context = this.gl;
    const miniGl = this;

    this.Uniform = class {
      constructor(e) {
        this.type = "float";
        Object.assign(this, e);
        const typeMap = { float: "1f", int: "1i", vec2: "2fv", vec3: "3fv", vec4: "4fv", mat4: "Matrix4fv" };
        this.typeFn = typeMap[this.type] || "1f";
      }

      update(location) {
        if (this.value === undefined || location === null) return;
        const fn = `uniform${this.typeFn}`;
        if (this.typeFn.indexOf("Matrix") === 0) {
          context[fn](location, this.transpose || false, this.value);
        } else {
          context[fn](location, this.value);
        }
      }

      getDeclaration(name, type, length) {
        if (this.excludeFrom === type) return "";

        if (this.type === "array") {
          return (
            this.value[0].getDeclaration(name, type, this.value.length) +
            `\nconst int ${name}_length = ${this.value.length};`
          );
        }

        if (this.type === "struct") {
          let nameNoPrefix = name.replace("u_", "");
          nameNoPrefix = nameNoPrefix.charAt(0).toUpperCase() + nameNoPrefix.slice(1);
          const fields = Object.entries(this.value)
            .map(([n, u]) => u.getDeclaration(n, type).replace(/^uniform/, ""))
            .join("");
          return `uniform struct ${nameNoPrefix} \n{\n${fields}\n} ${name}${length ? `[${length}]` : ""};`;
        }

        return `uniform ${this.type} ${name}${length ? `[${length}]` : ""};`;
      }
    };

    this.Attribute = class {
      constructor(e) {
        this.type = context.FLOAT;
        this.normalized = false;
        this.buffer = context.createBuffer();
        Object.assign(this, e);
      }

      update() {
        if (this.values) {
          context.bindBuffer(this.target, this.buffer);
          context.bufferData(this.target, this.values, context.STATIC_DRAW);
        }
      }

      attach(e, program) {
        const n = context.getAttribLocation(program, e);
        if (this.target === context.ARRAY_BUFFER) {
          context.bindBuffer(this.target, this.buffer);
          context.enableVertexAttribArray(n);
          context.vertexAttribPointer(n, this.size, this.type, this.normalized, 0, 0);
        }
        return n;
      }

      use(e) {
        context.bindBuffer(this.target, this.buffer);
        if (this.target === context.ARRAY_BUFFER) {
          context.enableVertexAttribArray(e);
          context.vertexAttribPointer(e, this.size, this.type, this.normalized, 0, 0);
        }
      }
    };

    this.Material = class {
      constructor(vertexShaders, fragments, uniforms = {}) {
        this.uniformInstances = [];
        const material = this;

        function getShader(type, source) {
          const shader = context.createShader(type);
          context.shaderSource(shader, source);
          context.compileShader(shader);
          if (!context.getShaderParameter(shader, context.COMPILE_STATUS)) {
            console.error(context.getShaderInfoLog(shader));
            throw new Error("Shader compilation error");
          }
          return shader;
        }

        function getUniformDeclarations(declaredUniforms, type) {
          return Object.entries(declaredUniforms)
            .map(([uniform, value]) => value.getDeclaration(uniform, type))
            .join("\n");
        }

        material.uniforms = uniforms;
        const prefix = "precision highp float;";

        const vertexSource = `
          ${prefix}
          attribute vec4 position;
          attribute vec2 uv;
          attribute vec2 uvNorm;
          ${getUniformDeclarations(miniGl.commonUniforms, "vertex")}
          ${getUniformDeclarations(uniforms, "vertex")}
          ${vertexShaders}
        `;

        const fragmentSource = `
          ${prefix}
          ${getUniformDeclarations(miniGl.commonUniforms, "fragment")}
          ${getUniformDeclarations(uniforms, "fragment")}
          ${fragments}
        `;

        material.program = context.createProgram();
        context.attachShader(material.program, getShader(context.VERTEX_SHADER, vertexSource));
        context.attachShader(material.program, getShader(context.FRAGMENT_SHADER, fragmentSource));
        context.linkProgram(material.program);

        if (!context.getProgramParameter(material.program, context.LINK_STATUS)) {
          console.error(context.getProgramInfoLog(material.program));
          throw new Error("Program linking error");
        }

        context.useProgram(material.program);
        material.attachUniforms(undefined, miniGl.commonUniforms);
        material.attachUniforms(undefined, material.uniforms);
      }

      attachUniforms(name, uniforms) {
        if (name === undefined) {
          Object.entries(uniforms).forEach(([n, u]) => this.attachUniforms(n, u));
        } else if (uniforms.type === "array") {
          uniforms.value.forEach((u, i) => this.attachUniforms(`${name}[${i}]`, u));
        } else if (uniforms.type === "struct") {
          Object.entries(uniforms.value).forEach(([u, i]) => this.attachUniforms(`${name}.${u}`, i));
        } else {
          this.uniformInstances.push({
            uniform: uniforms,
            location: context.getUniformLocation(this.program, name),
          });
        }
      }
    };

    this.PlaneGeometry = class {
      constructor() {
        this.width = 1;
        this.height = 1;
        this.vertexCount = 0;
        this.xSegCount = 0;
        this.ySegCount = 0;
        this.attributes = {
          position: new miniGl.Attribute({ target: context.ARRAY_BUFFER, size: 3 }),
          uv: new miniGl.Attribute({ target: context.ARRAY_BUFFER, size: 2 }),
          uvNorm: new miniGl.Attribute({ target: context.ARRAY_BUFFER, size: 2 }),
          index: new miniGl.Attribute({ target: context.ELEMENT_ARRAY_BUFFER, size: 3, type: context.UNSIGNED_SHORT }),
        };
      }

      setTopology(xSegs = 1, ySegs = 1) {
        this.xSegCount = xSegs;
        this.ySegCount = ySegs;
        this.vertexCount = (this.xSegCount + 1) * (this.ySegCount + 1);
        const quadCount = this.xSegCount * this.ySegCount * 2;

        this.attributes.uv.values = new Float32Array(2 * this.vertexCount);
        this.attributes.uvNorm.values = new Float32Array(2 * this.vertexCount);
        this.attributes.index.values = new Uint16Array(3 * quadCount);

        for (let y = 0; y <= this.ySegCount; y++) {
          for (let x = 0; x <= this.xSegCount; x++) {
            const i = y * (this.xSegCount + 1) + x;
            this.attributes.uv.values[2 * i] = x / this.xSegCount;
            this.attributes.uv.values[2 * i + 1] = 1 - y / this.ySegCount;
            this.attributes.uvNorm.values[2 * i] = (x / this.xSegCount) * 2 - 1;
            this.attributes.uvNorm.values[2 * i + 1] = 1 - (y / this.ySegCount) * 2;

            if (x < this.xSegCount && y < this.ySegCount) {
              const s = y * this.xSegCount + x;
              this.attributes.index.values[6 * s] = i;
              this.attributes.index.values[6 * s + 1] = i + 1 + this.xSegCount;
              this.attributes.index.values[6 * s + 2] = i + 1;
              this.attributes.index.values[6 * s + 3] = i + 1;
              this.attributes.index.values[6 * s + 4] = i + 1 + this.xSegCount;
              this.attributes.index.values[6 * s + 5] = i + 2 + this.xSegCount;
            }
          }
        }

        this.attributes.uv.update();
        this.attributes.uvNorm.update();
        this.attributes.index.update();
      }

      setSize(width = 1, height = 1) {
        this.width = width;
        this.height = height;
        this.attributes.position.values = new Float32Array(3 * this.vertexCount);

        const offsetX = width / -2;
        const offsetY = height / -2;
        const segWidth = width / this.xSegCount;
        const segHeight = height / this.ySegCount;

        for (let y = 0; y <= this.ySegCount; y++) {
          const posY = offsetY + y * segHeight;
          for (let x = 0; x <= this.xSegCount; x++) {
            const posX = offsetX + x * segWidth;
            const idx = y * (this.xSegCount + 1) + x;
            this.attributes.position.values[3 * idx] = posX;
            this.attributes.position.values[3 * idx + 1] = -posY;
            this.attributes.position.values[3 * idx + 2] = 0;
          }
        }

        this.attributes.position.update();
      }
    };

    this.Mesh = class {
      constructor(geometry, material) {
        this.geometry = geometry;
        this.material = material;
        this.attributeInstances = [];

        Object.entries(this.geometry.attributes).forEach(([e, attribute]) => {
          this.attributeInstances.push({
            attribute,
            location: attribute.attach(e, this.material.program),
          });
        });

        miniGl.meshes.push(this);
      }

      draw() {
        context.useProgram(this.material.program);
        this.material.uniformInstances.forEach(({ uniform, location }) => uniform.update(location));
        this.attributeInstances.forEach(({ attribute, location }) => attribute.use(location));
        context.drawElements(context.TRIANGLES, this.geometry.attributes.index.values.length, context.UNSIGNED_SHORT, 0);
      }
    };

    const identityMatrix = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
    this.commonUniforms = {
      projectionMatrix: new this.Uniform({ type: "mat4", value: identityMatrix }),
      modelViewMatrix: new this.Uniform({ type: "mat4", value: identityMatrix }),
      resolution: new this.Uniform({ type: "vec2", value: [1, 1] }),
      aspectRatio: new this.Uniform({ type: "float", value: 1 }),
    };
  }

  setSize(w = 640, h = 480) {
    this.width = w;
    this.height = h;
    this.canvas.width = w;
    this.canvas.height = h;
    this.gl.viewport(0, 0, w, h);
    this.commonUniforms.resolution.value = [w, h];
    this.commonUniforms.aspectRatio.value = w / h;
  }

  setOrthographicCamera() {
    this.commonUniforms.projectionMatrix.value = [
      2 / this.width, 0, 0, 0,
      0, 2 / this.height, 0, 0,
      0, 0, -0.001, 0,
      0, 0, 0, 1,
    ];
  }

  render() {
    this.gl.clearColor(0, 0, 0, 0);
    this.gl.clearDepth(1);
    this.meshes.forEach((m) => m.draw());
  }

  // Libera o contexto WebGL na GPU (o original nunca liberava).
  destroy() {
    const lose = this.gl.getExtension("WEBGL_lose_context");
    if (lose) lose.loseContext();
  }
}

const VERTEX_SHADER = `
vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 permute(vec4 x) { return mod289(((x*34.0)+1.0)*x); }
vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

float snoise(vec3 v) {
  const vec2 C = vec2(1.0/6.0, 1.0/3.0);
  const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
  vec3 i  = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);
  vec3 g = step(x0.yzx, x0.xyz);
  vec3 l = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);
  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;
  i = mod289(i);
  vec4 p = permute(permute(permute(i.z + vec4(0.0, i1.z, i2.z, 1.0)) + i.y + vec4(0.0, i1.y, i2.y, 1.0)) + i.x + vec4(0.0, i1.x, i2.x, 1.0));
  float n_ = 0.142857142857;
  vec3 ns = n_ * D.wyz - D.xzx;
  vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_);
  vec4 x = x_ *ns.x + ns.yyyy;
  vec4 y = y_ *ns.x + ns.yyyy;
  vec4 h = 1.0 - abs(x) - abs(y);
  vec4 b0 = vec4(x.xy, y.xy);
  vec4 b1 = vec4(x.zw, y.zw);
  vec4 s0 = floor(b0)*2.0 + 1.0;
  vec4 s1 = floor(b1)*2.0 + 1.0;
  vec4 sh = -step(h, vec4(0.0));
  vec4 a0 = b0.xzyw + s0.xzyw*sh.xxyy;
  vec4 a1 = b1.xzyw + s1.xzyw*sh.zzww;
  vec3 p0 = vec3(a0.xy,h.x);
  vec3 p1 = vec3(a0.zw,h.y);
  vec3 p2 = vec3(a1.xy,h.z);
  vec3 p3 = vec3(a1.zw,h.w);
  vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2, p2), dot(p3,p3)));
  p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
  vec4 m = max(0.6 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
  m = m * m;
  return 42.0 * dot(m*m, vec4(dot(p0,x0), dot(p1,x1), dot(p2,x2), dot(p3,x3)));
}

vec3 blendNormal(vec3 base, vec3 blend) { return blend; }
vec3 blendNormal(vec3 base, vec3 blend, float opacity) { return (blend * opacity + base * (1.0 - opacity)); }

varying vec3 v_color;

void main() {
  float time = u_time * u_global.noiseSpeed;
  vec2 noiseCoord = resolution * uvNorm * u_global.noiseFreq;
  float tilt = resolution.y / 2.0 * uvNorm.y;
  float incline = resolution.x * uvNorm.x / 2.0 * u_vertDeform.incline;
  float offset = resolution.x / 2.0 * u_vertDeform.incline * mix(u_vertDeform.offsetBottom, u_vertDeform.offsetTop, uv.y);

  float noise = snoise(vec3(
    noiseCoord.x * u_vertDeform.noiseFreq.x + time * u_vertDeform.noiseFlow,
    noiseCoord.y * u_vertDeform.noiseFreq.y,
    time * u_vertDeform.noiseSpeed + u_vertDeform.noiseSeed
  )) * u_vertDeform.noiseAmp;

  noise *= 1.0 - pow(abs(uvNorm.y), 2.0);
  noise = max(0.0, noise);

  vec3 pos = vec3(position.x, position.y + tilt + incline + noise - offset, position.z);

  v_color = u_baseColor;

  for (int i = 0; i < u_waveLayers_length; i++) {
    WaveLayers layer = u_waveLayers[i];
    float layerNoise = smoothstep(
      layer.noiseFloor,
      layer.noiseCeil,
      snoise(vec3(
        noiseCoord.x * layer.noiseFreq.x + time * layer.noiseFlow,
        noiseCoord.y * layer.noiseFreq.y,
        time * layer.noiseSpeed + layer.noiseSeed
      )) / 2.0 + 0.5
    );
    v_color = blendNormal(v_color, layer.color, pow(layerNoise, 4.));
  }

  gl_Position = projectionMatrix * modelViewMatrix * vec4(pos, 1.0);
}`;

const FRAGMENT_SHADER = `
varying vec3 v_color;

void main() {
  vec3 color = v_color;
  if (u_darken_top == 1.0) {
    vec2 st = gl_FragCoord.xy/resolution.xy;
    color.g -= pow(st.y + sin(-12.0) * st.x, u_shadow_power) * 0.4;
  }
  gl_FragColor = vec4(color, 1.0);
}`;

class Gradient {
  constructor(canvas, container, colors) {
    this.canvas = canvas;
    this.container = container;
    this.colors = colors;
    this.time = 0;
    this.last = 0;
    this.isPlaying = false;
    this.minigl = new MiniGl(canvas);
    this.init();
  }

  init() {
    const sectionColors = this.colors.map((hex) => normalizeColor(parseInt(hex.replace("#", "0x"), 16)));
    const U = this.minigl.Uniform;

    const uniforms = {
      u_time: new U({ value: 0 }),
      u_shadow_power: new U({ value: 5 }),
      u_darken_top: new U({ value: 0 }),
      u_global: new U({
        value: {
          noiseFreq: new U({ value: [0.00014, 0.00029], type: "vec2" }),
          noiseSpeed: new U({ value: 0.000005 }),
        },
        type: "struct",
      }),
      u_vertDeform: new U({
        value: {
          incline: new U({ value: 0 }),
          offsetTop: new U({ value: -0.5 }),
          offsetBottom: new U({ value: -0.5 }),
          noiseFreq: new U({ value: [3, 4], type: "vec2" }),
          noiseAmp: new U({ value: 320 }),
          noiseSpeed: new U({ value: 10 }),
          noiseFlow: new U({ value: 3 }),
          noiseSeed: new U({ value: 5 }),
        },
        type: "struct",
        excludeFrom: "fragment",
      }),
      u_baseColor: new U({ value: sectionColors[0], type: "vec3", excludeFrom: "fragment" }),
      u_waveLayers: new U({ value: [], excludeFrom: "fragment", type: "array" }),
    };

    for (let i = 1; i < sectionColors.length; i++) {
      uniforms.u_waveLayers.value.push(
        new U({
          value: {
            color: new U({ value: sectionColors[i], type: "vec3" }),
            noiseFreq: new U({ value: [2 + i / sectionColors.length, 3 + i / sectionColors.length], type: "vec2" }),
            noiseSpeed: new U({ value: 11 + 0.3 * i }),
            noiseFlow: new U({ value: 6.5 + 0.3 * i }),
            noiseSeed: new U({ value: 5 + 10 * i }),
            noiseFloor: new U({ value: 0.1 }),
            noiseCeil: new U({ value: 0.63 + 0.07 * i }),
          },
          type: "struct",
        })
      );
    }

    const material = new this.minigl.Material(VERTEX_SHADER, FRAGMENT_SHADER, uniforms);
    const geometry = new this.minigl.PlaneGeometry();
    this.mesh = new this.minigl.Mesh(geometry, material);
  }

  // Ajusta ao tamanho do CONTÊINER (não da janela). Retorna false se ele
  // estiver oculto (largura ou altura zero): aí não há o que desenhar.
  resize() {
    const width = this.container.clientWidth;
    const height = this.container.clientHeight;
    if (!width || !height) return false;

    this.minigl.setSize(width, height);
    this.minigl.setOrthographicCamera();
    const xSegCount = Math.ceil(width * 0.02);
    const ySegCount = Math.ceil(height * 0.05);
    this.mesh.geometry.setTopology(xSegCount, ySegCount);
    this.mesh.geometry.setSize(width, height);
    return true;
  }

  renderFrame() {
    this.mesh.material.uniforms.u_time.value = this.time;
    this.minigl.render();
  }

  animate = (timestamp) => {
    if (!this.isPlaying) return;
    this.time += Math.min(timestamp - this.last, 1000 / 15);
    this.last = timestamp;
    this.renderFrame();
    this.animationId = requestAnimationFrame(this.animate);
  };

  start() {
    if (this.isPlaying) return;
    this.isPlaying = true;
    this.animationId = requestAnimationFrame(this.animate);
  }

  stop() {
    this.isPlaying = false;
    if (this.animationId) cancelAnimationFrame(this.animationId);
  }

  destroy() {
    this.stop();
    this.minigl.destroy();
  }
}

// Padrões em constantes de MÓDULO (ver item 2 do comentário do topo).
const DEFAULT_COLORS = ["#38bdf8", "#ffffff", "#38bdf8", "#ffffff", "#38bdf8", "#ffffff"];
const DEFAULT_NOISE_FREQUENCY = [0.0001, 0.0009];
// O original passava { incline: 0.5, noiseAmp: 250, noiseFlow: 5 }, que nunca
// chegava ao shader (item 5). O visual da demonstração é, portanto, o dos
// valores iniciais; o padrão vazio mantém esse visual.
const DEFAULT_DEFORM = {};

function prefersReducedMotion() {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

function GradientWaveComponent({
  colors = DEFAULT_COLORS,
  isPlaying = true,
  className = "",
  shadowPower = 8,
  darkenTop = false,
  noiseSpeed = 0.00001,
  noiseFrequency = DEFAULT_NOISE_FREQUENCY,
  deform = DEFAULT_DEFORM,
}) {
  const containerRef = useRef(null);

  // Chave pelo CONTEÚDO das props: arrays/objetos novos com os mesmos
  // valores não recriam o WebGL (item 2).
  const configKey = JSON.stringify({ colors, isPlaying, shadowPower, darkenTop, noiseSpeed, noiseFrequency, deform });

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    const config = JSON.parse(configKey);

    const canvas = document.createElement("canvas");
    Object.assign(canvas.style, {
      position: "absolute", top: "0", left: "0", width: "100%", height: "100%", display: "block",
    });
    canvas.setAttribute("aria-hidden", "true"); // decorativo: leitores de tela ignoram
    container.appendChild(canvas);

    let gradient;
    try {
      gradient = new Gradient(canvas, container, config.colors);
    } catch (error) {
      // Sem WebGL: fica o fundo do elemento pai.
      console.error("Failed to initialize gradient:", error);
      container.removeChild(canvas);
      return undefined;
    }

    const uniforms = gradient.mesh.material.uniforms;
    uniforms.u_shadow_power.value = config.shadowPower;
    uniforms.u_darken_top.value = config.darkenTop ? 1 : 0;
    uniforms.u_global.value.noiseFreq.value = config.noiseFrequency;
    uniforms.u_global.value.noiseSpeed.value = config.noiseSpeed;
    // Item 5: atualiza o VALOR de cada uniform existente.
    Object.entries(config.deform).forEach(([key, value]) => {
      const uniform = uniforms.u_vertDeform.value[key];
      if (uniform) uniform.value = value;
    });

    const animate = config.isPlaying && !prefersReducedMotion();
    const sync = () => {
      const visible = gradient.resize();
      if (!visible) {
        gradient.stop(); // contêiner oculto: não gasta GPU nem bateria
      } else if (animate) {
        gradient.start();
      } else {
        gradient.renderFrame(); // quadro estático (pausado ou "reduzir movimento")
      }
    };
    sync();

    const observer = new ResizeObserver(sync);
    observer.observe(container);

    return () => {
      observer.disconnect();
      gradient.destroy();
      if (container.contains(canvas)) container.removeChild(canvas);
    };
  }, [configKey]);

  return <div ref={containerRef} className={`absolute inset-0 z-0 h-full w-full overflow-hidden ${className}`} />;
}

// memo: o fundo não renderiza de novo quando a página muda de estado
// (ex.: a cada tecla digitada no formulário de login).
export const GradientWave = memo(GradientWaveComponent);
