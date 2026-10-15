import { getRootEnv } from "./root-env.mjs";

const apiBaseUrl = getRootEnv("NEXT_PUBLIC_API_BASE_URL");
if (!apiBaseUrl) {
  throw new Error("NEXT_PUBLIC_API_BASE_URL 未配置，请检查项目根目录 .env");
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_API_BASE_URL: apiBaseUrl,
  },
};

export default nextConfig;
