export interface Hotspot {
  name: string;
  lat: number;
  lng: number;
}

/** Dense urban parks and known downtown cores used as demo spawn origins. */
export const HOTSPOTS: Hotspot[] = [
  { name: "San Francisco", lat: 37.7694, lng: -122.4862 },
  { name: "New York", lat: 40.7829, lng: -73.9654 },
  { name: "Tokyo", lat: 35.6895, lng: 139.6917 },
  { name: "London", lat: 51.5081, lng: -0.1281 },
  { name: "Paris", lat: 48.8584, lng: 2.2945 },
  { name: "Sydney", lat: -33.8688, lng: 151.2093 },
  { name: "Singapore", lat: 1.2897, lng: 103.8501 },
  { name: "Seoul", lat: 37.5665, lng: 126.978 },
  { name: "Berlin", lat: 52.52, lng: 13.405 },
  { name: "Madrid", lat: 40.4168, lng: -3.7038 },
  { name: "Rome", lat: 41.8902, lng: 12.4922 },
  { name: "Amsterdam", lat: 52.3676, lng: 4.9041 },
  { name: "Toronto", lat: 43.6532, lng: -79.3832 },
  { name: "Chicago", lat: 41.8781, lng: -87.6298 },
  { name: "Los Angeles", lat: 34.0522, lng: -118.2437 },
  { name: "Mexico City", lat: 19.4326, lng: -99.1332 },
  { name: "Sao Paulo", lat: -23.5505, lng: -46.6333 },
  { name: "Buenos Aires", lat: -34.6037, lng: -58.3816 },
  { name: "Cape Town", lat: -33.9249, lng: 18.4241 },
  { name: "Dubai", lat: 25.2048, lng: 55.2708 },
  { name: "Mumbai", lat: 19.076, lng: 72.8777 },
  { name: "Bangkok", lat: 13.7563, lng: 100.5018 },
  { name: "Hong Kong", lat: 22.3193, lng: 114.1694 },
  { name: "Taipei", lat: 25.033, lng: 121.5654 },
  { name: "Osaka", lat: 34.6937, lng: 135.5023 },
  { name: "Melbourne", lat: -37.8136, lng: 144.9631 },
  { name: "Auckland", lat: -36.8485, lng: 174.7633 },
  { name: "Vancouver", lat: 49.2827, lng: -123.1207 },
  { name: "Seattle", lat: 47.6062, lng: -122.3321 },
  { name: "Miami", lat: 25.7617, lng: -80.1918 },
  { name: "Zurich", lat: 47.3769, lng: 8.5417 },
  { name: "Vienna", lat: 48.2082, lng: 16.3738 },
  { name: "Prague", lat: 50.0755, lng: 14.4378 },
  { name: "Stockholm", lat: 59.3293, lng: 18.0686 },
  { name: "Oslo", lat: 59.9139, lng: 10.7522 },
  { name: "Lisbon", lat: 38.7223, lng: -9.1393 },
  { name: "Istanbul", lat: 41.0082, lng: 28.9784 },
  { name: "Cairo", lat: 30.0444, lng: 31.2357 },
  { name: "Nairobi", lat: -1.2921, lng: 36.8219 },
  { name: "Jakarta", lat: -6.2088, lng: 106.8456 },
];

export function jitterHotspot(spot: Hotspot, radiusMeters = 1800) {
  const ang = Math.random() * Math.PI * 2;
  const dist = Math.random() * radiusMeters;
  const dLat = (dist * Math.cos(ang)) / 111320;
  const dLng = (dist * Math.sin(ang)) / (111320 * Math.cos((spot.lat * Math.PI) / 180));
  return { lat: spot.lat + dLat, lng: spot.lng + dLng, city: spot.name };
}

export function randomHotspot() {
  return HOTSPOTS[Math.floor(Math.random() * HOTSPOTS.length)]!;
}
