# Guía de Deployment en Railway

## 🚀 Ventajas de Railway vs Vercel

| Característica | Railway | Vercel Free |
|---------------|---------|-------------|
| Timeout | 120 segundos | 10 segundos ⚠️ |
| Costo | $5 gratis/mes | Gratis limitado |
| Python Support | Nativo | Serverless |
| Oxylabs Compatible | ✅ Sí | ❌ No (timeout) |

## 📋 Pasos para Deploy

### 1. Crear cuenta en Railway

1. Ve a https://railway.app/
2. Haz clic en "Start a New Project"
3. Conecta tu cuenta de GitHub

### 2. Crear nuevo proyecto

1. En Railway dashboard, haz clic en "New Project"
2. Selecciona "Deploy from GitHub repo"
3. Autoriza Railway para acceder a tus repos
4. Selecciona el repositorio `upc-price-finder_v4_chatbot`
5. Railway detectará automáticamente que es una app Python

### 3. Configurar variables de entorno

En Railway dashboard → Settings → Variables:

```bash
GEMINI_KEY=tu_gemini_api_key_aqui
OXYLABS_USERNAME=sdatabunker
OXYLABS_PASSWORD=sDatabunker=123
PORT=5000
```

### 4. Configurar el servicio

1. Ve a Settings → Deploy
2. **Root Directory**: `upc-backend-clean`
3. **Build Command**: `pip install -r requirements-railway.txt`
4. **Start Command**: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`

### 5. Deploy

1. Railway hará deploy automáticamente
2. Espera 2-3 minutos
3. Railway te dará una URL pública como: `https://tu-app.up.railway.app`

### 6. Verificar deployment

Prueba estos endpoints:

```bash
# Health check
curl https://tu-app.up.railway.app/health

# Debug variables
curl https://tu-app.up.railway.app/api/debug

# Test price check
curl -X POST https://tu-app.up.railway.app/api/check_price \
  -H "Content-Type: application/json" \
  -d '{"query": "Coca Cola 600ml", "search_type": "shopping"}'
```

### 7. Actualizar Chrome Extension

En `upc-extension-react/background/background.js`, cambia:

```javascript
// Antes
const DEFAULT_BACKEND_URL = 'https://upc-backend-chatbot.vercel.app';

// Después
const DEFAULT_BACKEND_URL = 'https://tu-app.up.railway.app';
```

## 🔧 Archivos Necesarios

Ya creados en `upc-backend-clean/`:

- ✅ `app.py` - Flask application
- ✅ `requirements-railway.txt` - Dependencies
- ✅ `Procfile` - Railway start command
- ✅ `runtime.txt` - Python version
- ✅ `railway.json` - Railway configuration

## 📊 Costos Estimados

**Railway Starter Plan**:
- $5 USD gratis cada mes
- $0.000231 USD/GB RAM/minuto después
- Con tráfico moderado: ~$5-10/mes
- Mucho más barato que Vercel Pro ($20/mes)

## 🐛 Troubleshooting

### Si el deploy falla:

1. Revisa los logs en Railway dashboard
2. Verifica que las variables de entorno estén configuradas
3. Asegúrate que el Root Directory sea `upc-backend-clean`

### Si Oxylabs sigue con timeout:

El timeout está configurado a 120 segundos en Railway (vs 10 en Vercel), así que debería funcionar. Si aún falla:

1. Verifica tus credenciales de Oxylabs en `/api/debug`
2. Prueba con queries más específicas
3. Revisa el saldo de tu cuenta Oxylabs

## 📝 Notas Importantes

1. **Automatic Deploys**: Railway hace redeploy automático cuando haces push a GitHub
2. **Custom Domain**: Puedes agregar dominio personalizado en Settings → Domains
3. **Logs**: Railway tiene logs en tiempo real en el dashboard
4. **Scaling**: Puedes escalar verticalmente (más RAM/CPU) según necesites

## 🔄 Migración desde Vercel

1. Deploy en Railway siguiendo los pasos arriba
2. Prueba que todo funcione con curl
3. Actualiza la URL en la extensión
4. Puedes mantener Vercel como backup o eliminarlo

## ✅ Checklist de Deployment

- [ ] Cuenta Railway creada
- [ ] Repo conectado a Railway
- [ ] Variables de entorno configuradas
- [ ] Root directory configurado (`upc-backend-clean`)
- [ ] Deploy exitoso
- [ ] Health check funcionando
- [ ] API debug muestra variables SET
- [ ] Test de price check exitoso
- [ ] URL actualizada en Chrome Extension
- [ ] Extensión probada con nueva URL

---

**Última actualización**: Enero 22, 2026
