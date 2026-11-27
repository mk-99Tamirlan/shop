import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE = __ENV.BASE_URL || 'http://localhost:8080';
const VUS = parseInt(__ENV.VUS || '20');

export const options = {
  stages: [
    { duration: '10s', target: VUS },
    { duration: '30s', target: VUS },
    { duration: '5s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    'http_req_duration{name:search}': ['p(95)<200'],
    'http_req_duration{name:list}': ['p(95)<200'],
  },
};

export default function () {
  const q = Math.floor(Math.random() * 4096).toString(16).padStart(3, '0');
  const search = http.get(`${BASE}/products/search?q=${q}`, { tags: { name: 'search' } });
  check(search, { 'search HTTP 200': r => r.status === 200 });
  // случайный offset — чтобы не попадать в кэш
  const cat = Math.floor(Math.random() * 50) + 1;
  const offset = Math.floor(Math.random() * 200) * 20;
  const list = http.get(`${BASE}/products?category_id=${cat}&limit=20&offset=${offset}`, { tags: { name: 'list' } });
  check(list, { 'list HTTP 200': r => r.status === 200 });
  sleep(0.1);
}
