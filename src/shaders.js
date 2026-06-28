export const vertexShader = `
  varying vec2 vUv;
  varying vec3 vWorldPosition;

  void main() {
    vUv = uv;
    vec4 worldPosition = modelMatrix * vec4(position, 1.0);
    vWorldPosition = worldPosition.xyz;
    gl_Position = projectionMatrix * viewMatrix * worldPosition;
  }
`;

export const fragmentShader = `
  uniform sampler2D uMap;
  uniform vec3 uCameraPosition;

  varying vec2 vUv;
  varying vec3 vWorldPosition;

  void main() {
    // Sample texture
    vec4 tex = texture2D(uMap, vUv);

    // Subtle edge vignette per tile
    vec2 centered = vUv - 0.5;
    float edge = 1.0 - smoothstep(0.34, 0.86, length(centered));
    edge = mix(0.78, 1.0, edge);

    // Depth fade based on distance from camera
    float dist = distance(vWorldPosition, uCameraPosition);
    float depth = 1.0 - smoothstep(8.0, 22.0, dist);
    depth = mix(0.5, 1.0, depth);

    // Cinematic touch: desaturate far tiles (mix toward luma when depth is low)
    vec3 luma = vec3(dot(tex.rgb, vec3(0.299, 0.587, 0.114)));
    vec3 desaturatedColor = mix(luma, tex.rgb, mix(0.3, 1.0, depth));

    // Combine color elements
    vec3 color = desaturatedColor * edge * depth;

    gl_FragColor = vec4(color, tex.a);
  }
`;
