/**
 * LARUN.SPACE - Star Field Animation
 * Creates an animated starfield background using Canvas
 */

class StarField {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    if (!this.canvas) return;

    this.ctx = this.canvas.getContext('2d');
    this.stars = [];
    this.shootingStars = [];
    this.numStars = 200;
    this.maxShootingStars = 3;

    this.resize();
    this.init();
    this.animate();

    // Handle resize
    window.addEventListener('resize', () => this.resize());
  }

  resize() {
    if (!this.canvas) return;
    this.width = this.canvas.width = window.innerWidth;
    this.height = this.canvas.height = window.innerHeight;
  }

  init() {
    // Create regular stars
    for (let i = 0; i < this.numStars; i++) {
      this.stars.push(this.createStar());
    }
  }

  createStar() {
    return {
      x: Math.random() * this.width,
      y: Math.random() * this.height,
      size: Math.random() * 1.5 + 0.5,
      opacity: Math.random(),
      twinkleSpeed: Math.random() * 0.02 + 0.005,
      twinkleDirection: Math.random() > 0.5 ? 1 : -1
    };
  }

  createShootingStar() {
    const side = Math.random() > 0.5 ? 'top' : 'left';
    return {
      x: side === 'left' ? 0 : Math.random() * this.width,
      y: side === 'top' ? 0 : Math.random() * this.height * 0.5,
      length: Math.random() * 80 + 50,
      speed: Math.random() * 8 + 5,
      opacity: 1,
      active: true
    };
  }

  updateStar(star) {
    // Twinkle effect
    star.opacity += star.twinkleSpeed * star.twinkleDirection;

    if (star.opacity >= 1) {
      star.opacity = 1;
      star.twinkleDirection = -1;
    } else if (star.opacity <= 0.2) {
      star.opacity = 0.2;
      star.twinkleDirection = 1;
    }
  }

  updateShootingStar(star) {
    if (!star.active) return;

    star.x += star.speed;
    star.y += star.speed * 0.7;
    star.opacity -= 0.01;

    if (star.x > this.width || star.y > this.height || star.opacity <= 0) {
      star.active = false;
    }
  }

  drawStar(star) {
    this.ctx.beginPath();
    this.ctx.arc(star.x, star.y, star.size, 0, Math.PI * 2);
    this.ctx.fillStyle = `rgba(255, 255, 255, ${star.opacity})`;
    this.ctx.fill();
  }

  drawShootingStar(star) {
    if (!star.active) return;

    const gradient = this.ctx.createLinearGradient(
      star.x, star.y,
      star.x - star.length, star.y - star.length * 0.7
    );

    gradient.addColorStop(0, `rgba(255, 255, 255, ${star.opacity})`);
    gradient.addColorStop(1, 'rgba(255, 255, 255, 0)');

    this.ctx.beginPath();
    this.ctx.moveTo(star.x, star.y);
    this.ctx.lineTo(star.x - star.length, star.y - star.length * 0.7);
    this.ctx.strokeStyle = gradient;
    this.ctx.lineWidth = 1.5;
    this.ctx.stroke();

    // Bright head
    this.ctx.beginPath();
    this.ctx.arc(star.x, star.y, 2, 0, Math.PI * 2);
    this.ctx.fillStyle = `rgba(255, 255, 255, ${star.opacity})`;
    this.ctx.fill();
  }

  animate() {
    if (!this.canvas) return;

    // Clear canvas
    this.ctx.clearRect(0, 0, this.width, this.height);

    // Update and draw regular stars
    for (const star of this.stars) {
      this.updateStar(star);
      this.drawStar(star);
    }

    // Randomly add shooting stars
    if (Math.random() < 0.002 && this.shootingStars.filter(s => s.active).length < this.maxShootingStars) {
      this.shootingStars.push(this.createShootingStar());
    }

    // Update and draw shooting stars
    for (const star of this.shootingStars) {
      this.updateShootingStar(star);
      this.drawShootingStar(star);
    }

    // Clean up inactive shooting stars
    this.shootingStars = this.shootingStars.filter(s => s.active);

    requestAnimationFrame(() => this.animate());
  }
}

// Initialize star field
function initStars(canvasId) {
  // Check for reduced motion preference
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    return null;
  }
  return new StarField(canvasId);
}

// Auto-initialize if canvas exists
document.addEventListener('DOMContentLoaded', () => {
  const canvas = document.getElementById('star-canvas');
  if (canvas) {
    initStars('star-canvas');
  }
});
