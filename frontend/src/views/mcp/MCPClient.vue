<script lang="tsx" setup>
// 移除原有的MCP客户端相关代码，保留基本的Vue设置
</script>

<template>
  <n-layout class="main-layout" style="height: 100vh">
    <div class="promotion-page">
      <div class="content-wrapper">
        <h1 class="main-title animate-fade-in-down">通用智能体场景</h1>
        <div class="development-status animate-fade-in-up">
          <span class="status-text">正在开发中</span>
          <div class="pulse-dot"></div>
        </div>
        <div class="description animate-fade-in">
          <p>构建基于大模型的通用多智能场景，为您提供更智能、更高效的解决方案。</p>
          <p>有想法的同学，欢迎贡献代码 ; 目前缺前端后端同学，欢迎加入。</p>
          <p>敬请期待...</p>
        </div>
      </div>
    </div>
  </n-layout>
</template>

<style scoped>
.promotion-page {
  font-family: 'Helvetica Neue', Arial, sans-serif;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #6e8efb, #a777e3);
  border-radius: 8px;
  position: relative;
  overflow: hidden;
}

.promotion-page::before {
  content: '';
  position: absolute;
  top: -50%;
  left: -50%;
  width: 200%;
  height: 200%;
  background: radial-gradient(circle, rgba(255,255,255,0.1) 0%, rgba(255,255,255,0) 70%);
  animation: rotate 15s linear infinite;
  z-index: 0;
}

.content-wrapper {
  text-align: center;
  padding: 20px;
  position: relative;
  z-index: 1;
}

.main-title {
  font-size: 3rem;
  font-weight: 700;
  margin-bottom: 30px;
  color: #ffffff;
  text-shadow: 0 4px 8px rgba(0,0,0,0.2);
}

.development-status {
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 40px;
}

.status-text {
  font-size: 1.5rem;
  margin-right: 15px;
  color: #f8f9fa;
  font-weight: 500;
}

.pulse-dot {
  width: 15px;
  height: 15px;
  background-color: #ffeb3b;
  border-radius: 50%;
  animation: pulse 1.5s infinite;
}

@keyframes pulse {
  0% {
    transform: scale(0.9);
    box-shadow: 0 0 0 0 rgba(255, 235, 59, 0.7);
  }
  70% {
    transform: scale(1);
    box-shadow: 0 0 0 10px rgba(255, 235, 59, 0);
  }
  100% {
    transform: scale(0.9);
    box-shadow: 0 0 0 0 rgba(255, 235, 59, 0);
  }
}

@keyframes rotate {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.description {
  font-size: 1.2rem;
  line-height: 1.6;
  max-width: 600px;
  margin: 0 auto;
  color: #f1f3f5;
}

.description p {
  margin: 10px 0;
}

.main-layout {
  background-color: #fff;
  border-radius: 10px;
}

/* 添加动画类 */
.animate-fade-in-down {
  animation: fade-in-down 1s ease-out forwards;
  opacity: 0;
  transform: translateY(-20px);
}

.animate-fade-in-up {
  animation: fade-in-up 1s ease-out 0.3s forwards;
  opacity: 0;
  transform: translateY(20px);
}

.animate-fade-in {
  animation: fade-in 1s ease-out 0.6s forwards;
  opacity: 0;
}

@keyframes fade-in-down {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes fade-in-up {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes fade-in {
  to {
    opacity: 1;
  }
}
</style>
