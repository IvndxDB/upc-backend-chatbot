# UPC Price Finder Backend v4

Backend API para la extensión de Chrome DataBunker Price Checker v4, usando Oxylabs para scraping y Gemini para análisis de resultados.

## Características

- 🔍 **Búsqueda con Oxylabs**: Google Search y Google Shopping
- 🤖 **Análisis con Gemini**: Procesamiento inteligente de resultados
- ⚡ **Serverless**: Desplegado en Vercel
- 🌎 **Optimizado para México**: Búsquedas geo-localizadas

## Estructura

```
upc-backend-clean/
├── api/
│   └── check_price.py    # Endpoint principal
├── vercel.json           # Configuración de Vercel
├── requirements.txt      # Dependencias Python
└── README.md
```

## Configuración Local

1. Instalar dependencias:
```bash
pip install -r requirements.txt
```

2. Configurar variables de entorno (crear archivo `.env`):
```env
GEMINI_API_KEY=tu_api_key
OXYLABS_USERNAME=tu_username
OXYLABS_PASSWORD=tu_password
```

3. Probar localmente con Vercel CLI:
```bash
npm install -g vercel
vercel dev
```

## Desplegar en Vercel

1. Instalar Vercel CLI:
```bash
npm install -g vercel
```

2. Hacer login:
```bash
vercel login
```

3. Configurar variables de entorno en Vercel:
```bash
vercel env add GEMINI_API_KEY
vercel env add OXYLABS_USERNAME
vercel env add OXYLABS_PASSWORD
```

4. Desplegar:
```bash
vercel --prod
```

## API Endpoint

### POST `/api/check_price`

Busca precios de productos usando Oxylabs.

**Request Body:**
```json
{
  "query": "Coca Cola 600ml",
  "upc": "750105533307",
  "search_type": "shopping"
}
```

**Parámetros:**
- `query` (string): Nombre o descripción del producto
- `upc` (string, opcional): Código UPC del producto
- `search_type` (string, opcional): Tipo de búsqueda - `"shopping"` (default) o `"organic"`

**Response:**
```json
{
  "offers": [
    {
      "title": "Coca Cola 600ml",
      "price": 15.50,
      "currency": "MXN",
      "seller": "Walmart",
      "link": "https://...",
      "source": "oxylabs_shopping"
    }
  ],
  "summary": "Encontrados 5 productos",
  "total_offers": 5,
  "price_range": {
    "min": 15.50,
    "max": 18.90
  },
  "powered_by": "oxylabs + gemini"
}
```

## Integración con Frontend

El frontend debe hacer requests a tu URL de Vercel:

```javascript
const response = await fetch('https://tu-app.vercel.app/api/check_price', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    query: 'Producto a buscar',
    upc: '123456789',
    search_type: 'shopping'
  })
});

const data = await response.json();
console.log(data.offers);
```

## Servicios Utilizados

- **Oxylabs**: Web scraping (Google Search y Shopping)
- **Gemini**: Análisis y estructuración de resultados
- **Vercel**: Hosting serverless

## Límites y Costos

- Oxylabs: Basado en créditos por request
- Gemini: Gratis hasta cierto límite mensual
- Vercel: Plan gratuito incluye requests ilimitados

## Troubleshooting

### Error: "Oxylabs no configurado"
Verifica que las variables de entorno estén configuradas correctamente en Vercel.

### Error: "google-generativeai no disponible"
El sistema funcionará sin Gemini, pero los resultados no estarán procesados.

### Timeout en requests
Oxylabs puede tardar 10-30 segundos. Vercel tiene un límite de 10s en plan free, considera upgrade a Pro.
