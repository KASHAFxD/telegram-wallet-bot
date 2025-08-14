// Enhanced Device Fingerprinting Script (CDN-Free)
class EnhancedDeviceFingerprint {
    constructor() {
        this.fingerprint = null;
        this.components = {};
    }

    async generateFingerprint() {
        try {
            // Collect comprehensive device information
            this.components = {
                screen_resolution: `${screen.width}x${screen.height}x${screen.colorDepth}`,
                user_agent_hash: this.hashString(navigator.userAgent).toString(),
                timezone_offset: new Date().getTimezoneOffset(),
                language: navigator.language || navigator.userLanguage,
                platform: navigator.platform,
                hardware_concurrency: navigator.hardwareConcurrency || 0,
                memory: navigator.deviceMemory || 0,
                color_depth: screen.colorDepth,
                pixel_depth: screen.pixelDepth,
                available_resolution: `${screen.availWidth}x${screen.availHeight}`,
                touch_support: 'ontouchstart' in window || navigator.maxTouchPoints > 0,
                canvas_hash: await this.getCanvasFingerprint(),
                webgl_hash: await this.getWebGLFingerprint(),
                audio_hash: await this.getAudioFingerprint(),
                fonts_hash: this.getFontsFingerprint(),
                plugins_hash: this.getPluginsFingerprint(),
                timestamp: Date.now()
            };

            // Generate final fingerprint
            const combined = Object.values(this.components).join('|');
            this.fingerprint = this.hashString(combined);
            
            return {
                fingerprint: this.fingerprint,
                components: this.components
            };
        } catch (error) {
            console.error('Fingerprint generation error:', error);
            return {
                fingerprint: 'fallback_' + Date.now(),
                components: this.components
            };
        }
    }

    async getCanvasFingerprint() {
        try {
            const canvas = document.createElement('canvas');
            const ctx = canvas.getContext('2d');
            
            // Draw complex patterns
            ctx.textBaseline = 'top';
            ctx.font = '14px Arial';
            ctx.fillStyle = '#f60';
            ctx.fillRect(125, 1, 62, 20);
            ctx.fillStyle = '#069';
            ctx.fillText('Enhanced Device Fingerprint 🔒', 2, 15);
            ctx.fillStyle = 'rgba(102, 204, 0, 0.2)';
            ctx.fillText('Device Security Check', 4, 45);

            // Add geometric shapes
            ctx.beginPath();
            ctx.arc(50, 50, 20, 0, Math.PI * 2);
            ctx.fill();

            const canvasData = canvas.toDataURL();
            return this.hashString(canvasData).toString().slice(-16);
        } catch (e) {
            return 'canvas_unavailable';
        }
    }

    async getWebGLFingerprint() {
        try {
            const canvas = document.createElement('canvas');
            const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
            
            if (!gl) return 'webgl_unavailable';

            const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
            const vendor = gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL);
            const renderer = gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL);
            
            const webglInfo = `${vendor}|${renderer}|${gl.getParameter(gl.VERSION)}`;
            return this.hashString(webglInfo).toString().slice(-16);
        } catch (e) {
            return 'webgl_error';
        }
    }

    async getAudioFingerprint() {
        try {
            if (!window.AudioContext && !window.webkitAudioContext) {
                return 'audio_unavailable';
            }

            const context = new (window.AudioContext || window.webkitAudioContext)();
            const oscillator = context.createOscillator();
            const analyser = context.createAnalyser();
            const gainNode = context.createGain();

            oscillator.type = 'triangle';
            oscillator.frequency.value = 1000;
            gainNode.gain.value = 0;

            oscillator.connect(analyser);
            analyser.connect(gainNode);
            gainNode.connect(context.destination);

            oscillator.start();
            
            const frequencyData = new Uint8Array(analyser.frequencyBinCount);
            analyser.getByteFrequencyData(frequencyData);
            
            oscillator.stop();
            context.close();

            const audioHash = Array.from(frequencyData).slice(0, 30).join('');
            return this.hashString(audioHash).toString().slice(-16);
        } catch (e) {
            return 'audio_error';
        }
    }

    getFontsFingerprint() {
        try {
            const testFonts = [
                'Arial', 'Times New Roman', 'Courier New', 'Helvetica', 'Comic Sans MS',
                'Impact', 'Trebuchet MS', 'Verdana', 'Georgia', 'Palatino',
                'Apple Color Emoji', 'Segoe UI Emoji', 'Noto Color Emoji'
            ];
            
            const availableFonts = testFonts.filter(font => this.isFontAvailable(font));
            return this.hashString(availableFonts.join('|')).toString().slice(-16);
        } catch (e) {
            return 'fonts_error';
        }
    }

    isFontAvailable(fontName) {
        const testString = 'mmmmmmmmmmlli';
        const testSize = '72px';
        const baseFonts = ['monospace', 'sans-serif', 'serif'];
        
        const canvas = document.createElement('canvas');
        const context = canvas.getContext('2d');
        context.font = testSize + ' monospace';
        const baselineSize = context.measureText(testString).width;
        
        for (let baseFont of baseFonts) {
            context.font = testSize + ' ' + fontName + ', ' + baseFont;
            const newSize = context.measureText(testString).width;
            if (newSize !== baselineSize) {
                return true;
            }
        }
        return false;
    }

    getPluginsFingerprint() {
        try {
            const plugins = Array.from(navigator.plugins).map(p => `${p.name}|${p.version}`);
            return this.hashString(plugins.join('|')).toString().slice(-16);
        } catch (e) {
            return 'plugins_error';
        }
    }

    hashString(str) {
        let hash = 0;
        if (str.length === 0) return hash;
        for (let i = 0; i < str.length; i++) {
            const char = str.charCodeAt(i);
            hash = ((hash << 5) - hash) + char;
            hash = hash & hash; // Convert to 32-bit integer
        }
        return Math.abs(hash);
    }
}

// Global function for easy access
window.generateDeviceFingerprint = async function() {
    const fingerprinter = new EnhancedDeviceFingerprint();
    return await fingerprinter.generateFingerprint();
};
