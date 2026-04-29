import { createApp, computed, inject, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { createRouter, createWebHistory, useRoute, useRouter } from 'vue-router';
import './styles.css';
import { buildVehicleFitmentId, getVehicleFitmentId } from './data/mock-parts.js';

const API = import.meta.env.VITE_API_BASE || '/api';
const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || '';
const CONFIRMED_VEHICLE_KEY = 'confirmedVehicle';
const GUEST_PARTS_KEY = 'guestSearchParts';
const THEME_PREFERENCE_KEY = 'autodexx.themePreference';
let store;

function readThemePreference() {
  const stored = window.localStorage.getItem(THEME_PREFERENCE_KEY);
  return ['light', 'dark', 'manual'].includes(stored) ? stored : 'manual';
}

function applyThemePreference(preference) {
  if (preference === 'manual') {
    document.documentElement.removeAttribute('data-theme');
    return;
  }
  document.documentElement.setAttribute('data-theme', preference);
}

function readJson(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key) || window.sessionStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function normalizeVehicle(vehicle) {
  if (!vehicle) return null;
  return {
    _id: vehicle._id || vehicle.garageId || '',
    year: vehicle.year ? String(vehicle.year) : '',
    make: vehicle.make || '',
    model: vehicle.model || '',
    trim: vehicle.trim || '',
    engine: vehicle.engine || '',
    bodyStyle: vehicle.bodyStyle || '',
    driveType: vehicle.driveType || '',
    nickname: vehicle.nickname || '',
    isDefault: Boolean(vehicle.isDefault),
    fitmentId: vehicle.fitmentId || buildVehicleFitmentId(vehicle),
  };
}

function normalizeConfirmedVehicle(vehicle) {
  if (!vehicle) return null;
  return {
    year: vehicle.year ? String(vehicle.year) : '',
    make: vehicle.make || '',
    model: vehicle.model || '',
    trim: vehicle.trim || '',
    engine: vehicle.engine || '',
    bodyStyle: vehicle.bodyStyle || '',
    driveType: vehicle.driveType || '',
    fitmentId: vehicle.fitmentId || buildVehicleFitmentId(vehicle),
  };
}

function vehicleSummary(vehicle) {
  return [vehicle?.year, vehicle?.make, vehicle?.model, vehicle?.trim, vehicle?.engine].filter(Boolean).join(' ');
}

function vehicleKey(vehicle) {
  if (!vehicle) return '';
  return [vehicle.year, vehicle.make, vehicle.model, vehicle.trim || '', vehicle.engine || ''].join('|').toLowerCase();
}

function matchesVehicle(vehicleA, vehicleB) {
  return vehicleKey(vehicleA) === vehicleKey(vehicleB);
}

function partMatchesVehicle(part, vehicle) {
  if (!vehicle) return true;
  if (!part.supportedVehicleIds.length) return true;
  return part.supportedVehicleIds.includes(getVehicleFitmentId(vehicle));
}

function money(value) {
  if (value === null || value === undefined || value === '') return '$0.00';
  const normalized = Number(String(value).replace(/[^0-9.-]/g, ''));
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
  }).format(Number.isFinite(normalized) ? normalized : 0);
}

function resolveRetailerUrl(url, domain, fallbackUrl = '') {
  const raw = String(url || '').trim();
  if (!raw || raw === '#') return fallbackUrl || '#';
  if (/^https?:\/\//i.test(raw)) return raw;

  const base = String(fallbackUrl || '').trim() || (domain ? `https://www.${domain}` : '');
  if (!base) return raw;

  try {
    return new URL(raw, base).toString();
  } catch {
    return raw;
  }
}

function searchRecordToPart(record) {
  const retailers = Array.isArray(record?.retailers) ? record.retailers : [];
  const offersByTag = { recommended: [], 'lowest price': [], premium: [] };

  retailers.forEach((retailer) => {
    const tagged = Array.isArray(retailer?.taggedProducts) ? retailer.taggedProducts : [];
    tagged.forEach((entry) => {
      const tag = String(entry?.tag || '').toLowerCase();
      const product = entry?.product || {};
      if (!offersByTag[tag]) return;
      offersByTag[tag].push({
        retailer: retailer.retailerName || retailer.domain,
        domain: retailer.domain,
        price: product.price,
        // availability: product.availability || 'Unknown',
        warrantyLabel: tag,
        affiliateUrl: resolveRetailerUrl(product.href || retailer.targetUrl || '#', retailer.domain, retailer.targetUrl || ''),
        title: product.title || '',
        brand: product.brand || '',
        partNum: product.partNum || '',
        currency: product.currency || 'USD',
      });
    });
  });

  const fallbackOffers = offersByTag.recommended.length
    ? offersByTag.recommended
    : offersByTag['lowest price'].length
      ? offersByTag['lowest price']
      : offersByTag.premium;

  if (!fallbackOffers.length) {
    fallbackOffers.push({
      retailer: 'No successful retailer',
      domain: 'n/a',
      price: 0,
      availability: 'Unavailable',
      warrantyLabel: 'n/a',
      affiliateUrl: '#',
      title: '',
      brand: '',
      partNum: '',
      currency: 'USD',
    });
  }

  return {
    id: record.id,
    name: record.partQuery,
    brand: 'AutoDexx',
    category: 'Part Recommendations',
    isSearchRecord: true,
    description: `Multi-retailer recommendations for ${record.vehicleQuery}`,
    warranty: 'Retailer-specific',
    compatibilityNote: `Query vehicle: ${record.vehicleQuery}`,
    tags: (record.successfulDomains || []).slice(0, 3),
    supportedVehicleIds: [],
    offers: fallbackOffers,
    retailerOffersByTag: offersByTag,
    sourceRecord: record,
  };
}

function normalizeRetailerName(domain) {
  const names = {
    'oreillyauto.com': "O'Reilly Auto Parts",
    'autozone.com': 'AutoZone',
    'napaonline.com': 'NAPA',
    'ebay.com': 'eBay',
    'rockauto.com': 'RockAuto',
  };
  return names[domain] || domain;
}

function scrapeResponseToPart(payload, vehicleQuery, partQuery) {
  const results = payload?.results || {};
  const retailers = Object.entries(results).map(([domain, result]) => {
    const tagged = Array.isArray(result?.tagged_products) ? result.tagged_products : [];
    return {
      domain,
      retailerName: normalizeRetailerName(domain),
      targetUrl: resolveRetailerUrl(result?.source_url || result?.target_url || '#', domain),
      taggedProducts: tagged,
    };
  });

  const successfulDomains = retailers
    .filter((retailer) => Array.isArray(retailer.taggedProducts) && retailer.taggedProducts.length)
    .map((retailer) => retailer.domain);

  return searchRecordToPart({
    id: `guest-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    vehicleQuery,
    partQuery,
    successfulDomains,
    retailers,
  });
}

async function apiFetch(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    ...options,
  });

  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json') ? await response.json() : null;

  if (!response.ok) {
    throw new Error(payload?.error || payload?.detail || `Request failed (${response.status})`);
  }

  return payload;
}

let googleScriptPromise;
let templateCache = {};
function loadGoogleIdentityScript() {
  if (!GOOGLE_CLIENT_ID) return Promise.resolve(null);
  if (window.google?.accounts?.id) return Promise.resolve(window.google);
  if (!googleScriptPromise) {
    googleScriptPromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = 'https://accounts.google.com/gsi/client';
      script.async = true;
      script.defer = true;
      script.onload = () => resolve(window.google);
      script.onerror = () => reject(new Error('Failed to load Google sign-in.'));
      document.head.appendChild(script);
    });
  }
  return googleScriptPromise;
}

async function loadTemplates() {
  const entries = await Promise.all(
    ['SearchableDropdown', 'VehicleSelectionForm', 'GaragePage', 'LoginPage'].map(async (name) => {
      const response = await fetch(`/templates/${name}.html`);
      if (!response.ok) {
        throw new Error(`Failed to load template ${name}`);
      }
      return [name, await response.text()];
    }),
  );

  templateCache = Object.fromEntries(entries);
}

function createStore() {
  return reactive({
    authUser: null,
    authLoading: true,
    garageVehicles: [],
    garageLoading: false,
    garageError: '',
    partsLoading: false,
    searchLoading: false,
    searchStatusMessage: '',
    partsError: '',
    confirmedVehicle: readJson(CONFIRMED_VEHICLE_KEY, null),
    watchlistIds: [],
    themePreference: readThemePreference(),
    parts: readJson(GUEST_PARTS_KEY, []),

    get isAuthenticated() {
      return Boolean(this.authUser);
    },

    get activeVehicle() {
      return this.confirmedVehicle || this.garageVehicles.find((vehicle) => vehicle.isDefault) || null;
    },

    get watchlistParts() {
      return this.parts.filter((part) => this.watchlistIds.includes(part.id));
    },

    get recentParts() {
      return this.parts.slice(0, 3);
    },

    async refreshParts() {
      if (!this.authUser) {
        this.parts = readJson(GUEST_PARTS_KEY, []);
        this.watchlistIds = [];
        return;
      }

      this.partsLoading = true;
      this.partsError = '';

      try {
        const [searches, watchlist] = await Promise.all([
          apiFetch('/parts/searches'),
          apiFetch('/parts/watchlist'),
        ]);

        const byId = new Map();
        searches.forEach((record) => byId.set(record.id, record));
        watchlist.forEach((record) => byId.set(record.id, record));

        this.parts = Array.from(byId.values()).map(searchRecordToPart);
        this.watchlistIds = watchlist.map((item) => item.id);
        window.sessionStorage.removeItem(GUEST_PARTS_KEY);
      } catch (error) {
        this.partsError = error.message;
      } finally {
        this.partsLoading = false;
      }
    },

    async searchRecommendations({ vehicleQuery, partQuery }) {
      const retailers = [
        "O'Reilly Auto Parts",
        'AutoZone',
        'NAPA',
        'eBay',
        'RockAuto',
      ];
      let stepIndex = 0;

      this.searchLoading = true;
      this.partsError = '';
      this.searchStatusMessage = `Getting part recommendations from ${retailers[0]}...`;

      const ticker = window.setInterval(() => {
        stepIndex = (stepIndex + 1) % retailers.length;
        this.searchStatusMessage = `Getting part recommendations from ${retailers[stepIndex]}...`;
      }, 1200);

      try {
        let part;
        if (this.authUser) {
          const payload = await apiFetch('/parts/search', {
            method: 'POST',
            body: JSON.stringify({ vehicleQuery, partQuery }),
          });

          if (payload.status === 'error') {
            throw new Error(payload.message || 'Autodexx is not available at the moment. Please try again later.');
          }
          part = searchRecordToPart(payload.search);
        } else {
          // /search returns application/x-ndjson streaming with keepalive newlines
          // followed by the JSON result as the final line.
          const raw = await fetch(`${API}/search`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ vehicle_query: vehicleQuery, part_query: partQuery }),
          });
          if (!raw.ok) {
            const errBody = await raw.json().catch(() => ({}));
            throw new Error(errBody?.error || errBody?.detail || `Request failed (${raw.status})`);
          }
          const text = await raw.text();
          const lastLine = text.trim().split('\n').filter(Boolean).pop() || '{}';
          const payload = JSON.parse(lastLine);

          if (payload.status === 'error') {
            throw new Error(payload.message || 'Autodexx is not available at the moment. Please try again later.');
          }
          part = scrapeResponseToPart(payload, vehicleQuery, partQuery);
        }

        this.parts = [part, ...this.parts.filter((entry) => entry.id !== part.id)];
        if (!this.authUser) {
          window.sessionStorage.setItem(GUEST_PARTS_KEY, JSON.stringify(this.parts));
        }
        return part;
      } finally {
        window.clearInterval(ticker);
        this.searchLoading = false;
        this.searchStatusMessage = '';
      }
    },

    async clearSearchHistory() {
      this.partsError = '';

      if (this.authUser) {
        await apiFetch('/parts/searches', { method: 'DELETE' });
        await this.refreshParts();
        return;
      }

      this.parts = [];
      window.sessionStorage.removeItem(GUEST_PARTS_KEY);
    },

    persistConfirmedVehicle(vehicle) {
      const normalized = normalizeConfirmedVehicle(vehicle);
      this.confirmedVehicle = normalized;
      if (!normalized) {
        window.sessionStorage.removeItem(CONFIRMED_VEHICLE_KEY);
        return;
      }
      window.sessionStorage.setItem(CONFIRMED_VEHICLE_KEY, JSON.stringify(normalized));
    },

    clearConfirmedVehicle() {
      this.persistConfirmedVehicle(null);
    },

    syncConfirmedVehicle() {
      if (!this.garageVehicles.length) return;
      if (this.confirmedVehicle) {
        const matching = this.garageVehicles.find((vehicle) => matchesVehicle(vehicle, this.confirmedVehicle));
        if (matching) {
          this.persistConfirmedVehicle(matching);
          return;
        }
      }
      const defaultVehicle = this.garageVehicles.find((vehicle) => vehicle.isDefault) || this.garageVehicles[0];
      if (defaultVehicle) {
        this.persistConfirmedVehicle(defaultVehicle);
      }
    },

    async refreshUser() {
      this.authLoading = true;
      try {
        this.authUser = await apiFetch('/users/me');
      } catch {
        this.authUser = null;
      } finally {
        this.authLoading = false;
      }

      if (this.authUser) {
        await this.refreshGarage();
        await this.refreshParts();
      } else {
        this.garageVehicles = [];
        this.garageError = '';
        this.parts = [];
        this.watchlistIds = [];
      }
    },

    async refreshGarage() {
      if (!this.authUser) {
        this.garageVehicles = [];
        return;
      }

      this.garageLoading = true;
      this.garageError = '';
      try {
        const vehicles = await apiFetch('/garage');
        this.garageVehicles = vehicles.map(normalizeVehicle);
        this.syncConfirmedVehicle();
      } catch (error) {
        this.garageError = error.message;
      } finally {
        this.garageLoading = false;
      }
    },

    async login(username, password) {
      this.authUser = await apiFetch('/users/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      });
      this.clearConfirmedVehicle();
      await this.refreshGarage();
      await this.refreshParts();
    },

    async register(username, email, password) {
      this.authUser = await apiFetch('/users/register', {
        method: 'POST',
        body: JSON.stringify({ username, email, password }),
      });
      this.clearConfirmedVehicle();
      await this.refreshGarage();
      await this.refreshParts();
    },

    async loginWithGoogle(credential) {
      this.authUser = await apiFetch('/users/google', {
        method: 'POST',
        body: JSON.stringify({ credential }),
      });
      this.clearConfirmedVehicle();
      await this.refreshGarage();
      await this.refreshParts();
    },

    async logout() {
      await apiFetch('/users/logout', { method: 'POST' });
      this.authUser = null;
      this.garageVehicles = [];
      this.garageError = '';
      this.clearConfirmedVehicle();
      this.parts = [];
      this.watchlistIds = [];
    },

    setThemePreference(preference) {
      if (!['light', 'dark', 'manual'].includes(preference)) return;
      this.themePreference = preference;
      window.localStorage.setItem(THEME_PREFERENCE_KEY, preference);
      applyThemePreference(preference);
    },

    async addVehicle(vehicle) {
      const savedVehicle = normalizeVehicle(
        await apiFetch('/garage', {
          method: 'POST',
          body: JSON.stringify(vehicle),
        }),
      );
      await this.refreshGarage();
      this.persistConfirmedVehicle(savedVehicle);
      return savedVehicle;
    },

    async updateVehicleNickname(id, nickname) {
      const updatedVehicle = normalizeVehicle(
        await apiFetch(`/garage/${id}`, {
          method: 'PUT',
          body: JSON.stringify({ nickname }),
        }),
      );
      this.garageVehicles = this.garageVehicles.map((vehicle) => (vehicle._id === id ? updatedVehicle : vehicle));
      if (matchesVehicle(updatedVehicle, this.confirmedVehicle)) {
        this.persistConfirmedVehicle(updatedVehicle);
      }
    },

    async setDefaultVehicle(id) {
      const updatedVehicle = normalizeVehicle(
        await apiFetch(`/garage/${id}`, {
          method: 'PUT',
          body: JSON.stringify({ isDefault: true }),
        }),
      );
      this.garageVehicles = this.garageVehicles.map((vehicle) => ({
        ...vehicle,
        isDefault: vehicle._id === id,
      }));
      this.persistConfirmedVehicle(updatedVehicle);
    },

    async removeVehicle(id) {
      const removed = this.garageVehicles.find((vehicle) => vehicle._id === id);
      await apiFetch(`/garage/${id}`, { method: 'DELETE' });
      this.garageVehicles = this.garageVehicles.filter((vehicle) => vehicle._id !== id);
      if (removed && matchesVehicle(removed, this.confirmedVehicle)) {
        const fallback = this.garageVehicles.find((vehicle) => vehicle.isDefault) || this.garageVehicles[0] || null;
        this.persistConfirmedVehicle(fallback);
      }
    },

    isWatchlisted(partId) {
      return this.watchlistIds.includes(partId);
    },

    async toggleWatchlist(partId) {
      if (!this.authUser) {
        this.partsError = 'Please sign in to save searches to your watchlist.';
        return;
      }

      const isSaved = this.watchlistIds.includes(partId);
      if (isSaved) {
        await apiFetch(`/parts/watchlist/${partId}`, { method: 'DELETE' });
        this.watchlistIds = this.watchlistIds.filter((id) => id !== partId);
        return;
      }

      await apiFetch('/parts/watchlist', {
        method: 'POST',
        body: JSON.stringify({ partSearchId: partId }),
      });
      this.watchlistIds = [...this.watchlistIds, partId];
    },

    getPartById(partId) {
      return this.parts.find((part) => part.id === partId) || null;
    },

    compatibilityLabel(part) {
      if (!this.activeVehicle) return 'Select a vehicle to check fitment.';
      return partMatchesVehicle(part, this.activeVehicle)
        ? `Fits your selected vehicle: ${vehicleSummary(this.activeVehicle)}`
        : `Fitment is not confirmed for ${vehicleSummary(this.activeVehicle)}`;
    },
  });
}

function useStore() {
  return inject('store');
}

const SearchableDropdown = {
  props: {
    options: { type: Array, default: () => [] },
    modelValue: { type: String, default: '' },
    placeholder: { type: String, default: 'Select...' },
    disabled: { type: Boolean, default: false },
    loading: { type: Boolean, default: false },
    label: { type: String, default: '' },
  },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    const isOpen = ref(false);
    const search = ref('');
    const highlightIdx = ref(-1);
    const inputRef = ref(null);

    const filtered = computed(() => {
      if (!search.value) return props.options;
      const query = search.value.toLowerCase();
      return props.options.filter((option) => option.toLowerCase().includes(query));
    });

    const displayValue = computed(() => (isOpen.value ? search.value : props.modelValue || ''));

    function open() {
      if (props.disabled || props.loading) return;
      isOpen.value = true;
      search.value = '';
      highlightIdx.value = -1;
    }

    function close() {
      isOpen.value = false;
      search.value = '';
    }

    function select(value) {
      emit('update:modelValue', value);
      close();
    }

    function onInput(event) {
      search.value = event.target.value;
      highlightIdx.value = 0;
      if (!isOpen.value) isOpen.value = true;
    }

    function onKeydown(event) {
      if (!isOpen.value && (event.key === 'ArrowDown' || event.key === 'Enter')) {
        open();
        event.preventDefault();
        return;
      }
      if (event.key === 'ArrowDown') {
        highlightIdx.value = Math.min(highlightIdx.value + 1, filtered.value.length - 1);
        event.preventDefault();
      } else if (event.key === 'ArrowUp') {
        highlightIdx.value = Math.max(highlightIdx.value - 1, 0);
        event.preventDefault();
      } else if (event.key === 'Enter' && highlightIdx.value >= 0) {
        select(filtered.value[highlightIdx.value]);
        event.preventDefault();
      } else if (event.key === 'Escape') {
        close();
      }
    }

    function onBlur() {
      setTimeout(close, 150);
    }

    return { isOpen, search, highlightIdx, filtered, displayValue, inputRef, open, select, onInput, onKeydown, onBlur };
  },
  template: templateCache.SearchableDropdown,
};

const VehicleSelectionForm = {
  components: { SearchableDropdown },
  props: {
    tab: { type: String, required: true },
    years: { type: Array, default: () => [] },
    makes: { type: Array, default: () => [] },
    models: { type: Array, default: () => [] },
    trimOptions: { type: Array, default: () => [] },
    loadingYears: { type: Boolean, default: false },
    loadingMakes: { type: Boolean, default: false },
    loadingModels: { type: Boolean, default: false },
    loadingTrims: { type: Boolean, default: false },
    selectedYear: { type: String, default: '' },
    selectedMake: { type: String, default: '' },
    selectedModel: { type: String, default: '' },
    selectedTrim: { type: String, default: '' },
    selectedEngine: { type: String, default: '' },
    vinInput: { type: String, default: '' },
    vinError: { type: String, default: '' },
    vinLoading: { type: Boolean, default: false },
    showConfirm: { type: Boolean, default: false },
    confirmLabel: { type: String, default: 'Confirm Vehicle' },
    confirmDisabled: { type: Boolean, default: false },
  },
  emits: ['update:tab', 'update:selectedYear', 'update:selectedMake', 'update:selectedModel', 'update:selectedTrim', 'update:selectedEngine', 'update:vinInput', 'decode-vin', 'confirm', 'clear'],
  setup(props) {
    const vehicleSummaryText = computed(() => [props.selectedYear, props.selectedMake, props.selectedModel, props.selectedTrim, props.selectedEngine].filter(Boolean).join(' '));
    const canConfirm = computed(() => Boolean(props.selectedYear && props.selectedMake && props.selectedModel && props.selectedTrim && props.selectedEngine));
    const confirmButtonClass = computed(() => (props.confirmDisabled ? 'btn btn--outline' : 'btn btn--primary'));
    return { vehicleSummary: vehicleSummaryText, canConfirm, confirmButtonClass };
  },
  template: templateCache.VehicleSelectionForm,
};

function useVehicleSelection() {
  const tab = ref('manual');
  const years = ref([]);
  const makes = ref([]);
  const models = ref([]);
  const trimOptions = ref([]);

  const loadingYears = ref(false);
  const loadingMakes = ref(false);
  const loadingModels = ref(false);
  const loadingTrims = ref(false);

  const selectedYear = ref('');
  const selectedMake = ref('');
  const selectedModel = ref('');
  const selectedTrim = ref('');
  const selectedEngine = ref('');
  const selectedBodyStyle = ref('');
  const selectedDriveType = ref('');

  const vinInput = ref('');
  const vinError = ref('');
  const vinLoading = ref(false);
  const error = ref('');

  const aborts = { makes: null, models: null, trims: null };

  async function fetchYears() {
    loadingYears.value = true;
    try {
      years.value = (await apiFetch('/vehicles/years')).map(String);
    } catch (fetchError) {
      error.value = fetchError.message;
    } finally {
      loadingYears.value = false;
    }
  }

  async function fetchMakes() {
    aborts.makes?.abort();
    aborts.makes = new AbortController();
    loadingMakes.value = true;
    try {
      makes.value = await apiFetch('/vehicles/makes', { signal: aborts.makes.signal });
    } catch (fetchError) {
      if (fetchError.name !== 'AbortError') error.value = fetchError.message;
    } finally {
      loadingMakes.value = false;
    }
  }

  async function fetchModels() {
    if (!selectedYear.value || !selectedMake.value) return;
    aborts.models?.abort();
    aborts.models = new AbortController();
    loadingModels.value = true;
    try {
      models.value = await apiFetch(`/vehicles/models?year=${selectedYear.value}&make=${encodeURIComponent(selectedMake.value)}`, {
        signal: aborts.models.signal,
      });
    } catch (fetchError) {
      if (fetchError.name !== 'AbortError') error.value = fetchError.message;
    } finally {
      loadingModels.value = false;
    }
  }

  async function fetchTrimOptions() {
    if (!selectedYear.value || !selectedMake.value || !selectedModel.value) return;
    aborts.trims?.abort();
    aborts.trims = new AbortController();
    loadingTrims.value = true;
    try {
      trimOptions.value = await apiFetch(`/vehicles/trims?year=${selectedYear.value}&make=${encodeURIComponent(selectedMake.value)}&model=${encodeURIComponent(selectedModel.value)}`, {
        signal: aborts.trims.signal,
      });
    } catch (fetchError) {
      if (fetchError.name !== 'AbortError') error.value = fetchError.message;
    } finally {
      loadingTrims.value = false;
    }
  }

  async function applyVehicle(vehicle) {
    if (!vehicle) return;
    tab.value = 'manual';
    selectedYear.value = vehicle.year ? String(vehicle.year) : '';
    selectedMake.value = vehicle.make || '';
    selectedModel.value = '';
    selectedTrim.value = '';
    selectedEngine.value = '';
    selectedBodyStyle.value = '';
    selectedDriveType.value = '';

    await nextTick();
    if (selectedYear.value && selectedMake.value) {
      await fetchModels();
    }

    selectedModel.value = vehicle.model || '';

    if (selectedModel.value) {
      await fetchTrimOptions();
    }

    selectedTrim.value = vehicle.trim || '';
    selectedEngine.value = vehicle.engine || '';
    selectedBodyStyle.value = vehicle.bodyStyle || '';
    selectedDriveType.value = vehicle.driveType || '';
  }

  function clearSelection() {
    selectedYear.value = '';
    selectedMake.value = '';
    selectedModel.value = '';
    selectedTrim.value = '';
    selectedEngine.value = '';
    selectedBodyStyle.value = '';
    selectedDriveType.value = '';
    vinInput.value = '';
    vinError.value = '';
    models.value = [];
    trimOptions.value = [];
  }

  async function decodeVin() {
    vinError.value = '';
    const vin = vinInput.value.trim().toUpperCase();
    if (!/^[A-HJ-NPR-Z0-9]{17}$/.test(vin)) {
      vinError.value = 'Invalid VIN format. Must be 17 alphanumeric characters with no I, O, or Q.';
      return;
    }
    vinLoading.value = true;
    try {
      const decoded = await apiFetch(`/vehicles/vin/${vin}`);
      await applyVehicle(decoded);
    } catch (fetchError) {
      vinError.value = fetchError.message || 'Unable to decode VIN. Please enter the vehicle manually.';
    } finally {
      vinLoading.value = false;
    }
  }

  watch([selectedYear, selectedMake], async ([year, make], [previousYear, previousMake]) => {
    if (year === previousYear && make === previousMake) return;
    selectedModel.value = '';
    selectedTrim.value = '';
    selectedEngine.value = '';
    selectedBodyStyle.value = '';
    selectedDriveType.value = '';
    models.value = [];
    trimOptions.value = [];
    if (year && make) {
      await fetchModels();
    }
  });

  watch(selectedModel, async (model, previousModel) => {
    if (model === previousModel) return;
    selectedTrim.value = '';
    selectedEngine.value = '';
    selectedBodyStyle.value = '';
    selectedDriveType.value = '';
    trimOptions.value = [];
    if (model) {
      await fetchTrimOptions();
    }
  });

  async function initialize() {
    await fetchYears();
    await fetchMakes();
  }

  return {
    tab,
    years,
    makes,
    models,
    trimOptions,
    loadingYears,
    loadingMakes,
    loadingModels,
    loadingTrims,
    selectedYear,
    selectedMake,
    selectedModel,
    selectedTrim,
    selectedEngine,
    selectedBodyStyle,
    selectedDriveType,
    vinInput,
    vinError,
    vinLoading,
    error,
    initialize,
    applyVehicle,
    clearSelection,
    decodeVin,
  };
}

const HomePage = {
  setup() {
    const store = useStore();
    return { store, vehicleSummary, money };
  },
  template: `
    <div class="stack-lg">
      <section class="hero">
        <div class="hero-panel hero-copy stack-lg">
          <div>
            <p class="section-eyebrow">AutoDexx</p>
            <h1 class="page-title">Shop smarter for auto parts.</h1>
            <p class="page-subtitle">Find the best prices across top retailers in one search. AutoDexx matches parts to your exact vehicle so you never buy the wrong thing.</p>
          </div>
          <div class="hero-actions wrap">
            <router-link to="/search" class="btn btn--primary">New Search</router-link>
            <router-link to="/auth" class="btn btn--outline">Sign In</router-link>
          </div>
        </div>
        <div class="hero-panel hero-panel--stack">
          <div class="stat-card">
            <span class="stat-label">Garage Vehicles</span>
            <span class="stat-value">{{ store.garageVehicles.length }}</span>
          </div>
          <div class="stat-card">
            <span class="stat-label">Watchlist</span>
            <span class="stat-value">{{ store.watchlistParts.length }}</span>
          </div>
          <div class="stat-card">
            <span class="stat-label">Current Vehicle</span>
            <span class="stat-value" style="font-size:1.1rem;line-height:1.4;">{{ store.activeVehicle ? vehicleSummary(store.activeVehicle) : 'No vehicle selected' }}</span>
          </div>
        </div>
      </section>

      <section class="stack-md">
        <div class="section-header">
          <div>
            <p class="section-eyebrow">Featured Parts</p>
            <h2 class="card__title">Top parts for your vehicle</h2>
          </div>
        </div>
        <div class="parts-grid">
          <article v-for="part in store.recentParts" :key="part.id" class="part-card">
            <div class="part-card__top">
              <div>
                <p v-if="!part.isSearchRecord && part.category" class="muted">{{ part.category }}</p>
                <h3 class="part-card__title">{{ part.name }}</h3>
              </div>
              <strong class="price">{{ money(part.offers[0].price) }}</strong>
            </div>
            <p>{{ part.description }}</p>
            <div class="pill-row">
              <span v-for="tag in part.tags.slice(0, 3)" :key="tag" class="pill">{{ tag }}</span>
            </div>
            <div class="part-card__actions">
              <router-link :to="'/parts/' + part.id" class="btn btn--outline">View Part</router-link>
              <button class="btn btn--primary" @click="store.toggleWatchlist(part.id)">{{ store.isWatchlisted(part.id) ? 'Saved' : 'Save' }}</button>
            </div>
          </article>
        </div>
      </section>
    </div>
  `,
};

const GaragePage = {
  components: { VehicleSelectionForm },
  setup() {
    const store = useStore();
    const form = useVehicleSelection();
    const showForm = ref(!store.garageVehicles.length);
    const nickname = ref('');
    const formLoading = ref(false);
    const formError = ref('');
    const removeModalId = ref('');
    const editingNicknameId = ref('');
    const nicknameDraft = ref('');

    onMounted(async () => {
      await form.initialize();
    });

    function toggleAddVehicleForm() {
      showForm.value = !showForm.value;
      if (showForm.value) {
        form.clearSelection();
        nickname.value = '';
        formError.value = '';
      }
    }

    async function addVehicle() {
      formError.value = '';
      if (!form.selectedYear.value || !form.selectedMake.value || !form.selectedModel.value) {
        formError.value = 'Year, make, and model are required.';
        return;
      }

      formLoading.value = true;
      try {
        await store.addVehicle({
          year: form.selectedYear.value,
          make: form.selectedMake.value,
          model: form.selectedModel.value,
          trim: form.selectedTrim.value,
          engine: form.selectedEngine.value,
          bodyStyle: form.selectedBodyStyle.value,
          driveType: form.selectedDriveType.value,
          nickname: nickname.value.trim(),
        });
        nickname.value = '';
        showForm.value = false;
        form.clearSelection();
      } catch (error) {
        formError.value = error.message;
      } finally {
        formLoading.value = false;
      }
    }

    function beginNicknameEdit(vehicle) {
      editingNicknameId.value = vehicle._id;
      nicknameDraft.value = vehicle.nickname || '';
    }

    function cancelNicknameEdit() {
      editingNicknameId.value = '';
      nicknameDraft.value = '';
    }

    async function saveNickname(id) {
      try {
        await store.updateVehicleNickname(id, nicknameDraft.value.trim());
        cancelNicknameEdit();
      } catch (error) {
        formError.value = error.message;
      }
    }

    async function setDefault(id) {
      try {
        await store.setDefaultVehicle(id);
      } catch (error) {
        formError.value = error.message;
      }
    }

    function useVehicle(vehicle) {
      store.persistConfirmedVehicle(vehicle);
    }

    function confirmRemove(id) {
      removeModalId.value = id;
    }

    async function confirmRemoveAction() {
      if (!removeModalId.value) return;
      try {
        await store.removeVehicle(removeModalId.value);
      } catch (error) {
        formError.value = error.message;
      } finally {
        removeModalId.value = '';
      }
    }

    function vStr(vehicle) {
      return vehicleSummary(vehicle);
    }

    return {
      store,
      ...form,
      showForm,
      nickname,
      formLoading,
      formError,
      removeModalId,
      editingNicknameId,
      nicknameDraft,
      addVehicle,
      beginNicknameEdit,
      cancelNicknameEdit,
      saveNickname,
      setDefault,
      useVehicle,
      confirmRemove,
      confirmRemoveAction,
      vStr,
      toggleAddVehicleForm,
    };
  },
  template: templateCache.GaragePage,
};

const SearchPage = {
  components: { VehicleSelectionForm },
  setup() {
    const store = useStore();
    const form = useVehicleSelection();
    const query = ref('');
    const selectedGarageId = ref('');

    const filteredParts = computed(() => store.parts.filter((part) => partMatchesVehicle(part, store.activeVehicle)));
    const formVehicle = computed(() => ({
      year: form.selectedYear.value,
      make: form.selectedMake.value,
      model: form.selectedModel.value,
      trim: form.selectedTrim.value,
      engine: form.selectedEngine.value,
    }));
    const isVehicleConfirmed = computed(() => {
      if (!store.activeVehicle) return false;
      return matchesVehicle(formVehicle.value, store.activeVehicle);
    });

    watch(
      () => vehicleKey(store.activeVehicle),
      async () => {
        const matching = store.garageVehicles.find((vehicle) => matchesVehicle(vehicle, store.activeVehicle));
        selectedGarageId.value = matching?._id || '';
        if (store.activeVehicle) {
          await form.applyVehicle(store.activeVehicle);
        }
      },
      { immediate: true },
    );

    onMounted(async () => {
      await form.initialize();
      if (store.activeVehicle) {
        await form.applyVehicle(store.activeVehicle);
      }
    });

    function handleVehiclePick() {
      const vehicle = store.garageVehicles.find((entry) => entry._id === selectedGarageId.value);
      if (vehicle) {
        store.persistConfirmedVehicle(vehicle);
      } else {
        store.clearConfirmedVehicle();
      }
    }

    function confirmVehicle() {
      if (!form.selectedYear.value || !form.selectedMake.value || !form.selectedModel.value || !form.selectedTrim.value || !form.selectedEngine.value) {
        store.partsError = 'Year, make, model, trim, and engine are required before searching.';
        return;
      }

      store.persistConfirmedVehicle({
        year: form.selectedYear.value,
        make: form.selectedMake.value,
        model: form.selectedModel.value,
        trim: form.selectedTrim.value,
        engine: form.selectedEngine.value,
        bodyStyle: form.selectedBodyStyle.value,
        driveType: form.selectedDriveType.value,
      });
      store.partsError = '';
    }

    function clearVehicleSelection() {
      form.clearSelection();
      store.clearConfirmedVehicle();
      store.partsError = '';
    }

    async function submitSearch() {
      try {
        const partQuery = query.value.trim();
        if (!partQuery) {
          store.partsError = 'Please enter a part to search for.';
          return;
        }
        if (!store.activeVehicle) {
          store.partsError = 'Please select a vehicle before searching.';
          return;
        }

        await store.searchRecommendations({
          vehicleQuery: vehicleSummary(store.activeVehicle),
          partQuery,
        });
      } catch (error) {
        store.partsError = error.message || 'Autodexx is not available at the moment. Please try again later.';
      }
    }

    async function clearHistory() {
      try {
        await store.clearSearchHistory();
      } catch (error) {
        store.partsError = error.message || 'Unable to clear search history right now.';
      }
    }

    return {
      store,
      query,
      filteredParts,
      isVehicleConfirmed,
      selectedGarageId,
      handleVehiclePick,
      submitSearch,
      clearHistory,
      confirmVehicle,
      clearVehicleSelection,
      money,
      vehicleSummary,
      ...form,
    };
  },
  template: `
    <div class="stack-lg">
      <section class="section-header">
        <div>
          <p class="section-eyebrow">Parts Search</p>
          <h1 class="page-title">Find the right part at the right price.</h1>
          <p class="page-subtitle">Search thousands of parts across top retailers. Filter by your exact vehicle to see only confirmed-fit results.</p>
        </div>
      </section>

      <section class="card stack-md">
        <vehicle-selection-form
          :tab="tab"
          :years="years"
          :makes="makes"
          :models="models"
          :trim-options="trimOptions"
          :loading-years="loadingYears"
          :loading-makes="loadingMakes"
          :loading-models="loadingModels"
          :loading-trims="loadingTrims"
          :selected-year="selectedYear"
          :selected-make="selectedMake"
          :selected-model="selectedModel"
          :selected-trim="selectedTrim"
          :selected-engine="selectedEngine"
          :vin-input="vinInput"
          :vin-error="vinError"
          :vin-loading="vinLoading"
          :show-confirm="true"
          :confirm-disabled="isVehicleConfirmed"
          confirm-label="Confirm Vehicle"
          @update:tab="tab = $event"
          @update:selectedYear="selectedYear = $event"
          @update:selectedMake="selectedMake = $event"
          @update:selectedModel="selectedModel = $event"
          @update:selectedTrim="selectedTrim = $event"
          @update:selectedEngine="selectedEngine = $event"
          @update:vinInput="vinInput = $event"
          @decode-vin="decodeVin"
          @confirm="confirmVehicle"
          @clear="clearVehicleSelection"
        />

        <div v-if="!store.isAuthenticated" class="guest-banner">
          Guest mode: searches are kept only for this browser session. Sign in to save history and watchlist data.
        </div>
      </section>

      <section class="card stack-md">
        <div class="search-action-grid">
          <div class="form-group">
            <label for="part-query">Enter part</label>
            <input id="part-query" v-model="query" class="form-input" placeholder="Brake pads, filters, suspension..." />
          </div>
          <div class="form-group search-btn-group">
            <button class="btn btn--primary" @click="submitSearch" :disabled="store.searchLoading">{{ store.searchLoading ? 'Searching...' : 'Search' }}</button>
          </div>
        </div>

        <div class="form-group" v-if="store.garageVehicles.length">
          <label for="garage-selection">Use saved vehicle</label>
          <select id="garage-selection" v-model="selectedGarageId" class="form-select" @change="handleVehiclePick">
            <option value="">No saved vehicle override</option>
            <option v-for="vehicle in store.garageVehicles" :key="vehicle._id" :value="vehicle._id">{{ vehicleSummary(vehicle) }}</option>
          </select>
        </div>
      </section>

      <section v-if="store.searchLoading" class="card stack-sm">
        <h3 class="card__title">Searching Retailers - Please be patient, this may take a while!</h3>
        <p>{{ store.searchStatusMessage }}</p>
      </section>

      <section v-if="store.partsError" class="card stack-sm">
        <h3 class="card__title">Search Error</h3>
        <p>{{ store.partsError }}</p>
      </section>

      <div class="inline-row inline-row--end">
        <button class="btn btn--danger" @click="clearHistory" :disabled="store.searchLoading || store.parts.length === 0">Clear History</button>
      </div>

      <div v-if="filteredParts.length === 0" class="card empty-state">
        <div class="empty-state__title">No recommendation searches yet.</div>
        <p>Run a search to fetch recommendations from all supported retailers.</p>
      </div>

      <section v-else class="parts-grid">
        <article v-for="part in filteredParts" :key="part.id" class="part-card">
          <div class="part-card__top">
            <div>
              <p v-if="!part.isSearchRecord && (part.brand || part.category)" class="muted">{{ [part.brand, part.category].filter(Boolean).join(' · ') }}</p>
              <h2 class="part-card__title">{{ part.name }}</h2>
            </div>
          </div>
          <p>{{ part.description }}</p>
          <div class="pill-row">
            <span v-for="tag in part.tags.slice(0, 2)" :key="tag" class="pill">{{ tag }}</span>
          </div>
          <div class="retailer-offers-list">
            <div v-for="offer in part.offers" :key="offer.retailer" class="retailer-offer-row">
              <span class="retailer-offer-name">{{ offer.retailer }}</span>
              <span class="retailer-offer-price price">{{ money(offer.price) }}</span>
              <a :href="offer.affiliateUrl" target="_blank" rel="noreferrer" class="btn btn--outline btn--sm">Shop</a>
            </div>
          </div>
          <div class="part-card__actions">
            <router-link :to="'/parts/' + part.id" class="btn btn--outline">View Details</router-link>
            <button class="btn btn--primary" @click="store.toggleWatchlist(part.id)">{{ store.isWatchlisted(part.id) ? 'Saved' : 'Save to Watchlist' }}</button>
          </div>
        </article>
      </section>
    </div>
  `,
};

const WatchlistPage = {
  setup() {
    const store = useStore();
    return { store, money };
  },
  template: `
    <div class="stack-lg">
      <section class="section-header">
        <div>
          <p class="section-eyebrow">Watchlist</p>
          <h1 class="page-title">Your saved parts.</h1>
          <p class="page-subtitle">Keep track of parts you're considering. Compare prices and availability before you buy.</p>
        </div>
      </section>

      <div v-if="store.watchlistParts.length === 0" class="card empty-state">
        <div class="empty-state__title">No saved parts yet.</div>
        <p>Use the save action from search or part details to build a shortlist.</p>
      </div>

      <section v-else class="watchlist-grid">
        <article v-for="part in store.watchlistParts" :key="part.id" class="part-card">
          <div class="part-card__top">
            <div>
              <p v-if="!part.isSearchRecord && (part.brand || part.category)" class="muted">{{ [part.brand, part.category].filter(Boolean).join(' · ') }}</p>
              <h2 class="part-card__title">{{ part.name }}</h2>
            </div>
            <strong class="price">{{ money(part.offers[0].price) }}</strong>
          </div>
          <p>{{ part.description }}</p>
          <div class="part-card__actions">
            <router-link :to="'/parts/' + part.id" class="btn btn--outline">View Details</router-link>
            <button class="btn btn--danger" @click="store.toggleWatchlist(part.id)">Remove</button>
          </div>
        </article>
      </section>
    </div>
  `,
};

const PartDetailPage = {
  setup() {
    const route = useRoute();
    const store = useStore();
    const part = computed(() => store.getPartById(route.params.id));
    const selectedOfferTag = ref('recommended');
    const offerTags = ['recommended', 'lowest price', 'premium'];

    const visibleOffers = computed(() => {
      if (!part.value) return [];
      const source = part.value.retailerOffersByTag?.[selectedOfferTag.value] || [];
      return source;
    });

    return { store, part, money, vehicleSummary, partMatchesVehicle, selectedOfferTag, offerTags, visibleOffers };
  },
  template: `
    <div v-if="!part" class="card empty-state">
      <div class="empty-state__title">Part not found.</div>
      <p>This part may have been removed or the link might be incorrect.</p>
    </div>
    <div v-else class="detail-layout">
      <section class="card stack-md">
        <div>
          <p v-if="!part.isSearchRecord && (part.brand || part.category)" class="muted">{{ [part.brand, part.category].filter(Boolean).join(' · ') }}</p>
          <h1 class="page-title" style="font-size:2.2rem;">{{ part.name }}</h1>
          <p class="page-subtitle">{{ part.description }}</p>
        </div>
        <div class="pill-row">
          <span v-for="tag in part.tags" :key="tag" class="pill">{{ tag }}</span>
        </div>
        <div class="part-card__actions">
          <button class="btn btn--primary" @click="store.toggleWatchlist(part.id)">{{ store.isWatchlisted(part.id) ? 'Remove from Watchlist' : 'Save to Watchlist' }}</button>
          <router-link to="/search" class="btn btn--outline">Back to Search</router-link>
        </div>
      </section>

      <aside class="stack-md">
        <section class="card">
          <h2 class="card__title">Available Offers</h2>
          <div class="pill-row" style="margin-bottom: 0.75rem;">
            <button
              v-for="tag in offerTags"
              :key="tag"
              class="btn btn--outline"
              :class="{ 'btn--outline-active': selectedOfferTag === tag }"
              @click="selectedOfferTag = tag"
            >
              {{ tag }}
            </button>
          </div>
          <div class="offer-grid">
            <article v-for="offer in visibleOffers" :key="offer.retailer + selectedOfferTag" class="offer-card stack-sm">
              <strong>{{ offer.retailer }}</strong>
              <span class="price">{{ money(offer.price) }}</span>
              <span class="muted" v-if="offer.title">{{ offer.title }}</span>
              <span class="muted">{{ offer.availability }}</span>
              <span class="muted">{{ offer.warrantyLabel }}</span>
              <a :href="offer.affiliateUrl" target="_blank" rel="noreferrer" class="btn btn--outline">Open Offer</a>
            </article>
          </div>
          <p class="muted" style="margin-top: 0.75rem;">*Part fitment is not guaranteed. You should always verify fitment before purchasing.</p>
        </section>

        <section class="card stack-sm">
          <h2 class="card__title">Selected Vehicle</h2>
          <p>{{ store.activeVehicle ? vehicleSummary(store.activeVehicle) : 'No confirmed vehicle selected.' }}</p>
          <router-link to="/garage" class="btn btn--outline">Choose Vehicle</router-link>
        </section>
      </aside>
    </div>
  `,
};

const AuthPage = {
  setup() {
    const store = useStore();
    const router = useRouter();
    const isRegister = ref(false);
    const username = ref('');
    const email = ref('');
    const password = ref('');
    const loading = ref(false);
    const error = ref('');
    const googleButtonRef = ref(null);
    const googleEnabled = Boolean(GOOGLE_CLIENT_ID);

    function toggleMode() {
      isRegister.value = !isRegister.value;
      error.value = '';
    }

    async function submit() {
      error.value = '';
      if (!username.value.trim() || !password.value) {
        error.value = 'Username and password are required.';
        return;
      }
      if (isRegister.value && !email.value.trim()) {
        error.value = 'Email is required.';
        return;
      }

      loading.value = true;
      try {
        if (isRegister.value) {
          await store.register(username.value.trim(), email.value.trim(), password.value);
        } else {
          await store.login(username.value.trim(), password.value);
        }
        router.push('/garage');
      } catch (submitError) {
        error.value = submitError.message;
      } finally {
        loading.value = false;
      }
    }

    onMounted(async () => {
      if (!googleEnabled || !googleButtonRef.value) return;
      try {
        await loadGoogleIdentityScript();
        if (!window.google?.accounts?.id || !googleButtonRef.value) return;
        window.google.accounts.id.initialize({
          client_id: GOOGLE_CLIENT_ID,
          callback: async (response) => {
            if (!response.credential) return;
            loading.value = true;
            error.value = '';
            try {
              await store.loginWithGoogle(response.credential);
              router.push('/garage');
            } catch (submitError) {
              error.value = submitError.message;
            } finally {
              loading.value = false;
            }
          },
        });
        googleButtonRef.value.innerHTML = '';
        window.google.accounts.id.renderButton(googleButtonRef.value, {
          theme: 'outline',
          size: 'large',
          width: 320,
          text: 'continue_with',
        });
      } catch (submitError) {
        error.value = submitError.message;
      }
    });

    return { isRegister, username, email, password, loading, error, toggleMode, submit, googleButtonRef, googleEnabled };
  },
  template: templateCache.LoginPage,
};

const SettingsPage = {
  setup() {
    const store = useStore();
    const router = useRouter();

    const options = [
      {
        value: 'manual',
        title: 'Automatic (Follow Browser Setting)',
        description: 'Use your browser or OS preference automatically.',
      },
      {
        value: 'light',
        title: 'Light Mode',
        description: 'Always use the light color scheme.',
      },
      {
        value: 'dark',
        title: 'Dark Mode',
        description: 'Always use the dark color scheme.',
      },
    ];

    async function logout() {
      await store.logout();
      router.push('/auth');
    }

    return { store, options, logout };
  },
  template: `
    <div class="stack-lg">
      <section class="section-header">
        <div>
          <p class="section-eyebrow">Settings</p>
          <h1 class="page-title">Appearance and Account</h1>
          <p class="page-subtitle">Manage your display preferences and account session.</p>
        </div>
      </section>

      <div v-if="!store.isAuthenticated" class="card empty-state">
        <div class="empty-state__title">Sign in to access settings.</div>
        <p>Once you're signed in, you can manage account and display preferences here.</p>
        <router-link to="/auth" class="btn btn--primary">Sign In</router-link>
      </div>

      <section v-else class="stack-md">
        <article class="card stack-md">
          <h2 class="card__title">Color Scheme</h2>
          <p class="muted">Choose how AutoDexx applies light and dark mode.</p>

          <div class="theme-options">
            <label v-for="option in options" :key="option.value" class="theme-option">
              <input
                type="radio"
                name="themePreference"
                :value="option.value"
                :checked="store.themePreference === option.value"
                @change="store.setThemePreference(option.value)"
              />
              <div>
                <strong>{{ option.title }}</strong>
                <p class="form-help">{{ option.description }}</p>
              </div>
            </label>
          </div>
        </article>

        <article class="card stack-sm">
          <h2 class="card__title">Session</h2>
          <p class="muted">Sign out of your account from this device.</p>
          <div>
            <button class="btn btn--danger" @click="logout">Log Out</button>
          </div>
        </article>
      </section>
    </div>
  `,
};

const NotFoundPage = {
  template: `
    <div class="card empty-state">
      <div class="empty-state__title">Page not found.</div>
      <p>The page you're looking for doesn't exist.</p>
      <router-link to="/" class="btn btn--primary">Go Home</router-link>
    </div>
  `,
};

const routes = [
  { path: '/', component: HomePage },
  { path: '/garage', component: GaragePage },
  { path: '/search', component: SearchPage },
  { path: '/watchlist', component: WatchlistPage },
  { path: '/parts/:id', component: PartDetailPage },
  { path: '/settings', component: SettingsPage },
  { path: '/auth', component: AuthPage },
  { path: '/:pathMatch(.*)*', component: NotFoundPage },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior() {
    return { top: 0 };
  },
});

const App = {
  setup() {
    const prefersDark = ref(window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false);
    let mediaQuery = null;
    let mediaListener = null;

    onMounted(() => {
      if (!window.matchMedia) return;
      mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
      mediaListener = (event) => {
        prefersDark.value = event.matches;
      };

      if (typeof mediaQuery.addEventListener === 'function') {
        mediaQuery.addEventListener('change', mediaListener);
      } else if (typeof mediaQuery.addListener === 'function') {
        mediaQuery.addListener(mediaListener);
      }
    });

    onUnmounted(() => {
      if (!mediaQuery || !mediaListener) return;
      if (typeof mediaQuery.removeEventListener === 'function') {
        mediaQuery.removeEventListener('change', mediaListener);
      } else if (typeof mediaQuery.removeListener === 'function') {
        mediaQuery.removeListener(mediaListener);
      }
    });

    const logoPath = computed(() => {
      const useDarkLogo = store.themePreference === 'dark' || (store.themePreference === 'manual' && prefersDark.value);
      return useDarkLogo ? '/autodexx_wide_glow.png' : '/autodexx_no_bkg_halo.png';
    });

    return {
      store,
      vehicleSummary,
      logoPath,
    };
  },
  template: `
    <div class="app-shell">
      <header class="site-header">
        <div class="header-inner">
          <router-link to="/" class="logo" aria-label="AutoDexx home">
            <img :src="logoPath" alt="AutoDexx" class="logo-image" />
          </router-link>
          <nav class="header-nav">
            <router-link to="/garage" class="nav-link">Garage</router-link>
            <router-link to="/search" class="nav-link">Search</router-link>
            <router-link to="/watchlist" class="nav-link">Watchlist</router-link>
            <router-link v-if="store.authUser" to="/settings" class="nav-user">{{ store.authUser.username }}</router-link>
            <router-link v-if="!store.authUser" to="/auth" class="nav-link">Sign In</router-link>
          </nav>
        </div>
        <div class="vehicle-bar">
          <span class="vehicle-bar__label">Current Vehicle:</span>
          <span v-if="store.activeVehicle" class="vehicle-bar__vehicle">{{ vehicleSummary(store.activeVehicle) }}</span>
          <router-link v-else to="/garage" class="vehicle-bar__prompt">Choose a vehicle</router-link>
        </div>
      </header>
      <main class="main-content">
        <router-view />
      </main>
    </div>
  `,
};

async function bootstrap() {
  await loadTemplates();

  SearchableDropdown.template = templateCache.SearchableDropdown;
  VehicleSelectionForm.template = templateCache.VehicleSelectionForm;
  GaragePage.template = templateCache.GaragePage;
  AuthPage.template = templateCache.LoginPage;

  store = createStore();
  applyThemePreference(store.themePreference);
  await store.refreshUser();

  const app = createApp(App);
  app.provide('store', store);
  app.component('searchable-dropdown', SearchableDropdown);
  app.component('vehicle-selection-form', VehicleSelectionForm);
  app.use(router);
  app.mount('#app');
}

bootstrap();
