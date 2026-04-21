import { createApp, computed, inject, nextTick, onMounted, reactive, ref, watch } from 'vue';
import { createRouter, createWebHistory, useRoute, useRouter } from 'vue-router';
import './styles.css';
import { buildVehicleFitmentId, getVehicleFitmentId, mockParts } from './data/mock-parts.js';

const API = import.meta.env.VITE_API_BASE || '/api';
const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || '';
const CONFIRMED_VEHICLE_KEY = 'confirmedVehicle';
const WATCHLIST_KEY = 'autodexx.vue.watchlist';
const DEFAULT_WATCHLIST = ['tie-rod-end', 'ceramic-brake-pad-kit'];
let store;

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
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
  }).format(value);
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
    throw new Error(payload?.error || `Request failed (${response.status})`);
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
    confirmedVehicle: readJson(CONFIRMED_VEHICLE_KEY, null),
    watchlistIds: readJson(WATCHLIST_KEY, DEFAULT_WATCHLIST),
    parts: mockParts,

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
      } else {
        this.garageVehicles = [];
        this.garageError = '';
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
    },

    async register(username, email, password) {
      this.authUser = await apiFetch('/users/register', {
        method: 'POST',
        body: JSON.stringify({ username, email, password }),
      });
      this.clearConfirmedVehicle();
      await this.refreshGarage();
    },

    async loginWithGoogle(credential) {
      this.authUser = await apiFetch('/users/google', {
        method: 'POST',
        body: JSON.stringify({ credential }),
      });
      this.clearConfirmedVehicle();
      await this.refreshGarage();
    },

    async logout() {
      await apiFetch('/users/logout', { method: 'POST' });
      this.authUser = null;
      this.garageVehicles = [];
      this.garageError = '';
      this.clearConfirmedVehicle();
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

    toggleWatchlist(partId) {
      this.watchlistIds = this.watchlistIds.includes(partId)
        ? this.watchlistIds.filter((id) => id !== partId)
        : [...this.watchlistIds, partId];
      window.localStorage.setItem(WATCHLIST_KEY, JSON.stringify(this.watchlistIds));
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
  },
  emits: ['update:tab', 'update:selectedYear', 'update:selectedMake', 'update:selectedModel', 'update:selectedTrim', 'update:selectedEngine', 'update:vinInput', 'decode-vin', 'confirm', 'clear'],
  setup(props) {
    const vehicleSummaryText = computed(() => [props.selectedYear, props.selectedMake, props.selectedModel, props.selectedTrim, props.selectedEngine].filter(Boolean).join(' '));
    const canConfirm = computed(() => Boolean(props.selectedYear && props.selectedMake && props.selectedModel));
    return { vehicleSummary: vehicleSummaryText, canConfirm };
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
            <p class="section-eyebrow">AutoDEXX</p>
            <h1 class="page-title">Shop smarter for auto parts.</h1>
            <p class="page-subtitle">Find the best prices across top retailers in one search. AutoDEXX matches parts to your exact vehicle so you never buy the wrong thing.</p>
          </div>
          <div class="hero-actions wrap">
            <router-link to="/garage" class="btn btn--primary">Open Garage</router-link>
            <router-link to="/search" class="btn btn--outline">Browse Parts</router-link>
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
                <p class="muted">{{ part.category }}</p>
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
      if (store.activeVehicle) {
        await form.applyVehicle(store.activeVehicle);
      }
    });

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
    };
  },
  template: templateCache.GaragePage,
};

const SearchPage = {
  setup() {
    const store = useStore();
    const query = ref('');
    const category = ref('All');
    const selectedGarageId = ref('');

    const categories = computed(() => ['All', ...new Set(store.parts.map((part) => part.category))]);

    const filteredParts = computed(() => {
      return store.parts.filter((part) => {
        const queryMatch = !query.value || [part.name, part.brand, part.category, ...part.tags].join(' ').toLowerCase().includes(query.value.toLowerCase());
        const categoryMatch = category.value === 'All' || part.category === category.value;
        const vehicleMatch = partMatchesVehicle(part, store.activeVehicle);
        return queryMatch && categoryMatch && vehicleMatch;
      });
    });

    watch(
      () => vehicleKey(store.activeVehicle),
      () => {
        const matching = store.garageVehicles.find((vehicle) => matchesVehicle(vehicle, store.activeVehicle));
        selectedGarageId.value = matching?._id || '';
      },
      { immediate: true },
    );

    function handleVehiclePick() {
      const vehicle = store.garageVehicles.find((entry) => entry._id === selectedGarageId.value);
      if (vehicle) {
        store.persistConfirmedVehicle(vehicle);
      } else {
        store.clearConfirmedVehicle();
      }
    }

    return { store, query, category, categories, filteredParts, selectedGarageId, handleVehiclePick, money, vehicleSummary };
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
        <div class="search-filter-grid">
          <div class="form-group">
            <label for="part-query">Search parts</label>
            <input id="part-query" v-model="query" class="form-input" placeholder="Brake pads, filters, suspension..." />
          </div>
          <div class="form-group">
            <label for="category">Category</label>
            <select id="category" v-model="category" class="form-select">
              <option v-for="option in categories" :key="option" :value="option">{{ option }}</option>
            </select>
          </div>
          <div class="form-group" v-if="store.garageVehicles.length">
            <label for="garage-selection">Use saved vehicle</label>
            <select id="garage-selection" v-model="selectedGarageId" class="form-select" @change="handleVehiclePick">
              <option value="">No vehicle filter</option>
              <option v-for="vehicle in store.garageVehicles" :key="vehicle._id" :value="vehicle._id">{{ vehicleSummary(vehicle) }}</option>
            </select>
          </div>
        </div>
        <div class="vehicle-summary">
          <div class="vehicle-summary__text">{{ store.activeVehicle ? vehicleSummary(store.activeVehicle) : 'No confirmed vehicle selected' }}</div>
          <div class="vehicle-summary__actions">
            <router-link to="/garage" class="btn btn--outline">Manage Garage</router-link>
            <button class="btn btn--outline" @click="store.clearConfirmedVehicle()" :disabled="!store.activeVehicle">Clear Vehicle</button>
          </div>
        </div>
      </section>

      <div v-if="filteredParts.length === 0" class="card empty-state">
        <div class="empty-state__title">No parts matched this filter.</div>
        <p>Try a broader search or clear the vehicle filter.</p>
      </div>

      <section v-else class="parts-grid">
        <article v-for="part in filteredParts" :key="part.id" class="part-card">
          <div class="part-card__top">
            <div>
              <p class="muted">{{ part.brand }} · {{ part.category }}</p>
              <h2 class="part-card__title">{{ part.name }}</h2>
            </div>
            <strong class="price">{{ money(part.offers[0].price) }}</strong>
          </div>
          <p>{{ part.description }}</p>
          <div class="pill-row">
            <span class="pill fitment-pill">{{ store.compatibilityLabel(part) }}</span>
            <span v-for="tag in part.tags.slice(0, 2)" :key="tag" class="pill">{{ tag }}</span>
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
              <p class="muted">{{ part.brand }} · {{ part.category }}</p>
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
    return { store, part, money, vehicleSummary, partMatchesVehicle };
  },
  template: `
    <div v-if="!part" class="card empty-state">
      <div class="empty-state__title">Part not found.</div>
      <p>This part may have been removed or the link might be incorrect.</p>
    </div>
    <div v-else class="detail-layout">
      <section class="card stack-md">
        <div>
          <p class="muted">{{ part.brand }} · {{ part.category }}</p>
          <h1 class="page-title" style="font-size:2.2rem;">{{ part.name }}</h1>
          <p class="page-subtitle">{{ part.description }}</p>
        </div>
        <div class="pill-row">
          <span v-for="tag in part.tags" :key="tag" class="pill">{{ tag }}</span>
        </div>
        <div class="card">
          <h2 class="card__title">Compatibility</h2>
          <p>{{ store.compatibilityLabel(part) }}</p>
          <p class="muted">{{ part.compatibilityNote }}</p>
        </div>
        <div class="part-card__actions">
          <button class="btn btn--primary" @click="store.toggleWatchlist(part.id)">{{ store.isWatchlisted(part.id) ? 'Remove from Watchlist' : 'Save to Watchlist' }}</button>
          <router-link to="/search" class="btn btn--outline">Back to Search</router-link>
        </div>
      </section>

      <aside class="stack-md">
        <section class="card">
          <h2 class="card__title">Available Offers</h2>
          <div class="offer-grid">
            <article v-for="offer in part.offers" :key="offer.retailer" class="offer-card stack-sm">
              <strong>{{ offer.retailer }}</strong>
              <span class="price">{{ money(offer.price) }}</span>
              <span class="muted">{{ offer.availability }}</span>
              <span class="muted">{{ offer.warrantyLabel }}</span>
              <a :href="offer.affiliateUrl" target="_blank" rel="noreferrer" class="btn btn--outline">Open Offer</a>
            </article>
          </div>
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
    return { store, vehicleSummary };
  },
  template: `
    <div class="app-shell">
      <header class="site-header">
        <div class="header-inner">
          <router-link to="/" class="logo">AutoDEXX</router-link>
          <nav class="header-nav">
            <router-link to="/garage" class="nav-link">Garage</router-link>
            <router-link to="/search" class="nav-link">Search</router-link>
            <router-link to="/watchlist" class="nav-link">Watchlist</router-link>
            <span v-if="store.authUser" class="nav-user">{{ store.authUser.username }}</span>
            <router-link v-if="!store.authUser" to="/auth" class="nav-link">Sign In</router-link>
            <button v-else class="btn btn--outline btn--small" @click="store.logout()">Log Out</button>
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
  await store.refreshUser();

  const app = createApp(App);
  app.provide('store', store);
  app.component('searchable-dropdown', SearchableDropdown);
  app.component('vehicle-selection-form', VehicleSelectionForm);
  app.use(router);
  app.mount('#app');
}

bootstrap();