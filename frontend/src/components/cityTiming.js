// Shared with SplashScreen without importing CityScene (which would pull three.js into the eager bundle).
export const FLIGHT_S = 0.95; // camera flight from wherever it is into the billboard (was 2.4 s: kept snappy, under 1 s)
// fraction of the flight at which the scene hands over (the workspace fade starts): the last 20% is a close-up of the white
// face that the fade covers anyway, so waiting for it only added latency
export const ARRIVE_AT = 0.8;
