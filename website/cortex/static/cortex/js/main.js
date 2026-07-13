const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

/* ---- Scroll reveal ---- */
const io = new IntersectionObserver((entries) => {
    entries.forEach(e => {
        if (e.isIntersecting) {
            e.target.classList.add('in');
            io.unobserve(e.target);
        }
    });
}, { threshold: 0.12, rootMargin: '0px 0px -50px 0px' });
document.querySelectorAll('.reveal').forEach(el => io.observe(el));

/* ---- Duplicate marquee for seamless loop ---- */
const mq = document.getElementById('marquee');
if (mq) mq.innerHTML += mq.innerHTML;

/* ---- Cinematic cursor glow (smoothed) ---- */
const glow = document.getElementById('cursorGlow');
let gx = innerWidth / 2, gy = innerHeight / 2, tx = gx, ty = gy;
if (glow && !reduce) {
    addEventListener('pointermove', e => { tx = e.clientX; ty = e.clientY; }, { passive: true });
    (function loop() {
        gx += (tx - gx) * 0.12;
        gy += (ty - gy) * 0.12;
        glow.style.left = gx + 'px';
        glow.style.top = gy + 'px';
        requestAnimationFrame(loop);
    })();
} else if (glow) { glow.style.display = 'none'; }

/* ---- Card spotlight tracking ---- */
document.querySelectorAll('.card').forEach(card => {
    card.addEventListener('pointermove', e => {
        const r = card.getBoundingClientRect();
        card.style.setProperty('--mx', (e.clientX - r.left) + 'px');
        card.style.setProperty('--my', (e.clientY - r.top) + 'px');
    });
});

/* ---- Magnetic buttons ---- */
if (!reduce) {
    document.querySelectorAll('.magnetic').forEach(btn => {
        btn.addEventListener('pointermove', e => {
            const r = btn.getBoundingClientRect();
            const mx = e.clientX - r.left - r.width / 2;
            const my = e.clientY - r.top - r.height / 2;
            btn.style.transform = `translate(${mx * 0.18}px, ${my * 0.28}px)`;
        });
        btn.addEventListener('pointerleave', () => { btn.style.transform = ''; });
    });
}

/* ---- Scroll-driven: navbar, hero parallax, showcase tilt, back-to-top ---- */
const navbar = document.getElementById('navbar');
const hero = document.getElementById('hero');
const frame = document.getElementById('showFrame');
const toTop = document.getElementById('toTop');
let ticking = false;

function onScroll() {
    const y = scrollY;
    if (navbar) navbar.classList.toggle('scrolled', y > 20);

    if (!reduce) {
        // hero drifts up slightly and fades only a touch as you scroll past it
        if (hero) {
            const hp = Math.min(y / 800, 1);
            hero.style.transform = `translateY(${y * 0.06}px)`;
            hero.style.opacity = String(1 - hp * 0.3);
        }

        // showcase frame rises flat + scales up into view
        if (frame) {
            const r = frame.getBoundingClientRect();
            const prog = Math.min(Math.max((innerHeight - r.top) / (innerHeight * 0.9), 0), 1);
            const rx = 8 - prog * 8;          // 8deg -> 0
            const sc = 0.94 + prog * 0.06;    // 0.94 -> 1
            frame.style.transform = `rotateX(${rx}deg) scale(${sc})`;
        }
    }

    if (toTop) {
        const on = y > 500;
        toTop.style.opacity = on ? '1' : '0';
        toTop.style.pointerEvents = on ? 'auto' : 'none';
    }
    ticking = false;
}
addEventListener('scroll', () => {
    if (!ticking) { requestAnimationFrame(onScroll); ticking = true; }
}, { passive: true });
onScroll();

/* ---- Mobile menu ---- */
const links = document.getElementById('navLinks');
const menuToggle = document.getElementById('menuToggle');
if (menuToggle && links) {
    menuToggle.addEventListener('click', () => {
        const open = links.style.display === 'flex';
        links.style.display = open ? 'none' : 'flex';
        links.style.flexDirection = 'column';
        links.style.position = 'absolute';
        links.style.top = '72px';
        links.style.left = '16px';
        links.style.right = '16px';
        links.style.padding = '14px';
        links.style.borderRadius = '20px';
        links.style.background = 'rgba(12,13,15,0.92)';
        links.style.backdropFilter = 'blur(24px)';
        links.style.border = '1px solid rgba(255,255,255,0.10)';
    });
    links.querySelectorAll('a').forEach(a => a.addEventListener('click', () => {
        if (innerWidth <= 900) links.style.display = 'none';
    }));
}

/* ---- FAQ accordion (smooth, single-open) ---- */
document.querySelectorAll('.faq-q').forEach(q => {
    q.addEventListener('click', () => {
        const item = q.parentElement;
        const panel = item.querySelector('.faq-a');
        const isOpen = item.classList.contains('open');
        document.querySelectorAll('.faq-item.open').forEach(o => {
            o.classList.remove('open');
            o.querySelector('.faq-a').style.maxHeight = null;
        });
        if (!isOpen) {
            item.classList.add('open');
            panel.style.maxHeight = panel.scrollHeight + 'px';
        }
    });
});

/* ---- Animated agent-session demo (loops while in view) ---- */
/* Disabled — replaced with real screenshot/video showcase */
/* (function () { ... })(); */
