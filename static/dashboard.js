// ===============================
// CONFIG & SEEN STATE
// ===============================
const SEEN_ANNOUNCEMENTS_KEY = 'butler_seen_announcements';

async function loadConfig() {
    try {
        const res = await fetch('/user_config.json');
        if (!res.ok) return {};
        return await res.json();
    } catch (e) {
        console.error("Failed to load config:", e);
        return {};
    }
}

function applyTheme(theme) {
    let dark = false;
    if (theme === 'dark') dark = true;
    else if (theme === 'light') dark = false;
    else if (theme === 'auto') dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    document.body.classList.toggle('dark-theme', dark);
    document.body.classList.toggle('light-theme', !dark);
}

function getEmailAccounts(config) {
    if (!config) return [];
    if (Array.isArray(config.emails)) return config.emails;
    if (config.emails && Array.isArray(config.emails.accounts)) return config.emails.accounts;
    return [];
}

function isEmailsEnabled(config) {
    if (!config) return false;
    if (config.emails && typeof config.emails.enabled === 'boolean') return config.emails.enabled;
    return getEmailAccounts(config).length > 0;
}

function getWeatherEnabled(config) {
    if (!config) return true;
    if (config.weather && typeof config.weather.enabled === 'boolean') return config.weather.enabled;
    return true;
}

function getNewsEnabled(config) {
    if (!config) return true;
    if (config.news && typeof config.news.enabled === 'boolean') return config.news.enabled;
    return true;
}

// ===============================
// SEEN ANNOUNCEMENTS (LOCAL STATE)
// ===============================
function getSeenAnnouncements() {
    const stored = localStorage.getItem(SEEN_ANNOUNCEMENTS_KEY);
    return stored ? JSON.parse(stored) : {};
}

function markAnnouncementSeen(id, postedAt) {
    const seen = getSeenAnnouncements();
    seen[id] = postedAt;
    localStorage.setItem(SEEN_ANNOUNCEMENTS_KEY, JSON.stringify(seen));
}

function isAnnouncementSeen(id) {
    const seen = getSeenAnnouncements();
    return id in seen;
}

// ===============================
// ASSIGNMENT HELPER
// ===============================
function shortenAssignmentName(fullName, maxLen = 45) {
    if (fullName.length <= maxLen) return fullName;
    const parts = fullName.split(/[:\-–—]/);
    if (parts.length > 1) {
        const lastPart = parts[parts.length - 1].trim();
        if (lastPart.length > 10 && lastPart.length <= maxLen) return lastPart;
    }
    return fullName.substring(0, maxLen) + '…';
}

// ===============================
// REVEAL ANIMATION
// ===============================
function revealItems(container, items, formatter) {
    container.innerHTML = '';
    if (items.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'empty-state';
        empty.textContent = 'Your morning is clear.';
        container.appendChild(empty);
        return;
    }
    items.forEach((item, idx) => {
        const div = document.createElement('div');
        div.className = 'list-item reveal';
        if (item.url) div.dataset.url = item.url;
        if (item.announcementId) div.dataset.announcementId = item.announcementId;
        
        const html = formatter(item);
        if (typeof html === 'string') {
            div.innerHTML = html;
        } else {
            div.appendChild(html);
        }
        
        container.appendChild(div);
        // Stagger very slightly (10ms per item, imperceptible)
        setTimeout(() => div.classList.add('ready'), idx * 10);
    });
}

// ===============================
// CLICK HANDLERS
// ===============================
function setupClickHandlers(container) {
    container.addEventListener('click', (e) => {
        const item = e.target.closest('.list-item');
        if (!item) return;
        
        const url = item.dataset.url;
        const announcementId = item.dataset.announcementId;
        
        // Mark announcement as seen
        if (announcementId) {
            const posted = item.dataset.posted || new Date().toISOString();
            markAnnouncementSeen(announcementId, posted);
        }
        
        // Open in new tab
        if (url) {
            window.open(url, '_blank', 'noopener,noreferrer');
        }
    });
}

// ===============================
// FETCH DATA
// ===============================
async function fetchCanvasData() {
    try {
        const res = await fetch('/canvas_data');
        if (!res.ok) {
            console.warn(`Canvas data returned ${res.status}`);
            return { assignments: [], announcements: [] };
        }
        return await res.json();
    } catch (e) {
        console.error("Failed to fetch Canvas data:", e);
        return { assignments: [], announcements: [] };
    }
}

async function fetchWeather() {
    try {
        const res = await fetch('/weather');
        if (!res.ok) return null;
        return await res.json();
    } catch (e) {
        console.error("Failed to fetch weather:", e);
        return null;
    }
}

async function fetchNews() {
    try {
        const res = await fetch('/news');
        if (!res.ok) return [];
        const data = await res.json();
        return data.items || [];
    } catch (e) {
        console.error("Failed to fetch news:", e);
        return [];
    }
}

// ===============================
// FORMAT DATA
// ===============================
function formatAssignmentData(canvasData) {
    const now = new Date();
    now.setHours(0, 0, 0, 0);
    const oneWeekOut = new Date(now);
    oneWeekOut.setDate(oneWeekOut.getDate() + 7);
    oneWeekOut.setHours(23, 59, 59, 999);
    
    const today = new Date(now);
    today.setHours(0, 0, 0, 0);
    const tomorrowStart = new Date(today);
    tomorrowStart.setDate(tomorrowStart.getDate() + 1);

    return (canvasData.assignments || []).map(a => {
        const dueDate = a.due_at ? new Date(a.due_at) : null;
        const dueStr = dueDate 
            ? dueDate.toLocaleString([], { 
                month: 'short', 
                day: 'numeric', 
                hour: '2-digit', 
                minute: '2-digit' 
              })
            : 'No due date';

        let statusStr = '';
        if (a.submission) {
            if (a.submission.workflow_state === 'graded') {
                const grade = a.submission.grade || a.submission.score;
                statusStr = grade ? `Graded: ${grade}` : 'Graded';
            } else if (a.submission.workflow_state === 'submitted') {
                statusStr = 'Submitted';
            } else {
                statusStr = 'Not submitted';
            }
        } else {
            statusStr = 'Not submitted';
        }

        // Urgency: today or this week
        let urgency = null;
        if (dueDate) {
            const dueDateOnly = new Date(dueDate);
            dueDateOnly.setHours(0, 0, 0, 0);
            if (dueDateOnly.getTime() === today.getTime()) {
                urgency = 'today';
            } else if (dueDate >= tomorrowStart && dueDate <= oneWeekOut) {
                urgency = 'week';
            }
        }

        const isUrgent = dueDate && dueDate >= now && dueDate <= oneWeekOut;

        return {
            course: a.course,
            name: a.name,
            shortName: shortenAssignmentName(a.name),
            due: dueStr,
            dueDate: a.due_at,
            status: statusStr,
            url: a.submission?.preview_url || '',
            isUrgent: isUrgent,
            hasDueDate: !!dueDate,
            urgency: urgency
        };
    });
}

function formatAnnouncementData(canvasData) {
    const seen = getSeenAnnouncements();
    return (canvasData.announcements || []).map(a => {
        const id = `${a.course}_${a.title}`.replace(/\s+/g, '_');
        const posted = a.posted ? new Date(a.posted).toLocaleString([], {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        }) : '';
        return {
            course: a.course,
            title: a.title,
            posted: posted,
            id: id,
            isSeen: isAnnouncementSeen(id),
            url: a.url || ''
        };
    });
}

// ===============================
// SEARCH & SORT
// ===============================
function searchAssignments(assignments, query) {
    if (!query) return assignments;
    const lq = query.toLowerCase();
    return assignments.filter(a => 
        a.name.toLowerCase().includes(lq) || 
        a.course.toLowerCase().includes(lq)
    );
}

function sortAssignments(assignments, sortBy) {
    const sorted = [...assignments];
    const dateValue = (a, direction = 'asc') => {
        if (!a.hasDueDate || !a.dueDate) return direction === 'asc' ? Number.POSITIVE_INFINITY : Number.NEGATIVE_INFINITY;
        const d = new Date(a.dueDate);
        return isNaN(d.getTime()) ? (direction === 'asc' ? Number.POSITIVE_INFINITY : Number.NEGATIVE_INFINITY) : d.getTime();
    };
    switch(sortBy) {
        case 'due-desc':
            return sorted.sort((a, b) => dateValue(b, 'desc') - dateValue(a, 'desc'));
        case 'course':
            return sorted.sort((a, b) => a.course.localeCompare(b.course));
        case 'status':
            return sorted.sort((a, b) => a.status.localeCompare(b.status));
        case 'due-asc':
        default:
            return sorted.sort((a, b) => dateValue(a, 'asc') - dateValue(b, 'asc'));
    }
}

// ===============================
// RENDER DASHBOARD
// ===============================
async function renderDashboard() {
    console.log("Rendering dashboard...");
    applyTheme('auto');

    // Set an early greeting and loading line to avoid a blank minute
    const now = new Date();
    const greetingEl = document.getElementById('greeting');
    if (greetingEl) {
        if (now.getHours() < 12) greetingEl.textContent = 'Good morning.';
        else if (now.getHours() < 18) greetingEl.textContent = 'Good afternoon.';
        else greetingEl.textContent = 'Good evening.';
    }
    const timeWeatherEl = document.getElementById('time-weather');
    if (timeWeatherEl) {
        const loadingLines = [
            'Just one moment...',
            'At your service...',
            'Fetching your morning briefing...',
            'Putting the kettle on...',
            'Tidying your dashboard...'
        ];
        timeWeatherEl.textContent = loadingLines[Math.floor(Math.random() * loadingLines.length)];
    }

    const config = await loadConfig();
    applyTheme(config.theme || 'auto');

    const weatherEnabled = getWeatherEnabled(config);
    const newsEnabled = getNewsEnabled(config);

    const [canvasData, weatherData, newsData] = await Promise.all([
        fetchCanvasData(),
        fetchWeather(),
        newsEnabled ? fetchNews() : Promise.resolve([])
    ]);

    // Elements
    const assignmentsList = document.getElementById('assignments-list');
    const announcementsList = document.getElementById('announcements-list');
    const emailsSection = document.getElementById('emails-section');
    const toggleBtn = document.getElementById('toggle-assignments');
    const searchInput = document.getElementById('assignment-search');
    const sortSelect = document.getElementById('assignment-sort');
    const weatherSection = document.getElementById('weather-section');
    const newsSection = document.getElementById('news-section');
    const viewAllAssignmentsLink = document.getElementById('view-all-assignments');
    const viewAllAnnouncementsLink = document.getElementById('view-all-announcements');

    if (!assignmentsList || !announcementsList) {
        console.error("Dashboard elements missing");
        return;
    }

    // Format data
    const allAssignments = formatAssignmentData(canvasData);
    
    // Apply configured filters
    const assignmentFilters = config.assignmentFilters || {};
    const hideNoDueDate = assignmentFilters.hideNoDueDate !== false;
    const hideCompleted = assignmentFilters.hideCompleted || false;
    const defaultView = assignmentFilters.defaultView || 'week';
    
    let filteredAssignments = allAssignments;
    
    if (hideNoDueDate) {
        filteredAssignments = filteredAssignments.filter(a => a.hasDueDate);
    }
    
    if (hideCompleted) {
        filteredAssignments = filteredAssignments.filter(a => {
            const status = (a.status || '').toLowerCase();
            return !(status.startsWith('graded') || status.startsWith('submitted'));
        });
    }
    
    let showAllAssignments = false;
    const nowDate = new Date();
    const today = new Date(nowDate);
    today.setHours(0, 0, 0, 0);
    const oneWeekOut = new Date(today);
    oneWeekOut.setDate(oneWeekOut.getDate() + 7);
    oneWeekOut.setHours(23, 59, 59, 999);
    const applyDefaultViewFilter = (items) => {
        if (defaultView === 'day') {
            return items.filter(a => {
                if (!a.hasDueDate) return false;
                const dueDate = new Date(a.dueDate);
                dueDate.setHours(0, 0, 0, 0);
                return dueDate.getTime() === today.getTime();
            });
        }
        if (defaultView === 'week') {
            return items.filter(a => {
                if (!a.hasDueDate) return false;
                const dueDate = new Date(a.dueDate);
                dueDate.setHours(0, 0, 0, 0);
                return dueDate >= today && dueDate <= oneWeekOut;
            });
        }
        return items;
    };
    
    const allAnnouncements = formatAnnouncementData(canvasData);
    const unseenAnnouncements = allAnnouncements.filter(a => !a.isSeen);

    // Render assignments with urgency coloring
    function formatAssignment(a) {
        const el = document.createElement('div');
        const primary = document.createElement('div');
        primary.className = 'list-item-primary';
        primary.textContent = `${a.course} — ${a.shortName}`;
        const secondary = document.createElement('div');
        secondary.className = 'list-item-secondary';
        secondary.textContent = `Due ${a.due}`;
        const status = document.createElement('div');
        status.className = 'list-item-status';
        status.textContent = a.status;
        el.appendChild(primary);
        el.appendChild(secondary);
        el.appendChild(status);
        return el;
    }

    function renderAssignmentsFiltered(searchQuery = '', sortBy = 'due-asc') {
        let toRender = filteredAssignments;
        if (searchQuery) {
            toRender = searchAssignments(toRender, searchQuery);
        }
        toRender = sortAssignments(toRender, sortBy);
        
        if (!showAllAssignments) {
            toRender = applyDefaultViewFilter(toRender);
        }
        
        revealItems(assignmentsList, toRender, formatAssignment);
        
        // Add urgency coloring
        document.querySelectorAll('#assignments-list .list-item').forEach((item, idx) => {
            const assignment = toRender[idx];
            if (assignment && assignment.urgency) {
                item.classList.add(`urgency-${assignment.urgency}`);
            }
        });
        
        setupClickHandlers(assignmentsList);
    }

    renderAssignmentsFiltered();

    // Search and sort event listeners
    if (searchInput && sortSelect) {
        searchInput.addEventListener('input', (e) => {
            renderAssignmentsFiltered(e.target.value, sortSelect.value);
        });
        sortSelect.addEventListener('change', (e) => {
            renderAssignmentsFiltered(searchInput.value, e.target.value);
        });
    }

    // Render announcements
    let showAllAnnouncements = false;
    const announcementsToShow = unseenAnnouncements.slice(0, 2);
    
    function formatAnnouncement(a) {
        const el = document.createElement('div');
        const primary = document.createElement('div');
        primary.className = 'list-item-primary';
        primary.textContent = `${a.course} — ${a.title}`;
        const secondary = document.createElement('div');
        secondary.className = 'list-item-secondary';
        secondary.textContent = `Posted ${a.posted}`;
        if (a.isSeen) {
            secondary.style.opacity = '0.4';
        }
        el.appendChild(primary);
        el.appendChild(secondary);
        return el;
    }

    revealItems(announcementsList, announcementsToShow, formatAnnouncement);
    setupClickHandlers(announcementsList);

    // Toggle button
    toggleBtn.addEventListener('click', () => {
        if (toggleBtn.textContent === 'Show all') {
            showAllAssignments = true;
            renderAssignmentsFiltered(searchInput?.value || '', sortSelect?.value || 'due-asc');
            toggleBtn.textContent = 'Show less';
        } else {
            showAllAssignments = false;
            renderAssignmentsFiltered();
            toggleBtn.textContent = 'Show all';
        }
    });

    // Weather
    if (weatherEnabled && weatherData && weatherSection) {
        weatherSection.style.display = 'block';
        const tempEl = document.getElementById('weather-temp');
        const condEl = document.getElementById('weather-condition');
        if (tempEl && condEl) {
            tempEl.textContent = `${weatherData.temp}° — ${weatherData.condition}`;
            if (weatherData.humidity) {
                condEl.textContent = `Humidity ${weatherData.humidity}%`;
            }
        }
    }

    // News
    if (newsEnabled && newsData.length > 0 && newsSection) {
        newsSection.style.display = 'block';
        const newsList = document.getElementById('news-list');
        const newsArticles = newsData.slice(0, 5);
        
        function formatNews(n) {
            const el = document.createElement('div');
            const primary = document.createElement('div');
            primary.className = 'list-item-primary';
            primary.textContent = n.title;
            const secondary = document.createElement('div');
            secondary.className = 'list-item-secondary';
            secondary.textContent = n.source;
            el.appendChild(primary);
            el.appendChild(secondary);
            return el;
        }
        
        revealItems(newsList, newsArticles, formatNews);
        setupClickHandlers(newsList);
    }

    // Emails (collapsed by default)
    const emailAccounts = getEmailAccounts(config);
    if (isEmailsEnabled(config) && emailAccounts.length > 0 && emailsSection) {
        emailsSection.style.display = 'block';
        const emailsList = document.getElementById('emails-list');
        const emails = emailAccounts.slice(0, 3);
        
        function formatEmail(e) {
            const el = document.createElement('div');
            el.className = 'list-item-primary';
            el.textContent = e.subject || '(No subject)';
            return el;
        }
        
        revealItems(emailsList, emails, formatEmail);
        setupClickHandlers(emailsList);
    }

    // Greeting & time
    let nowFinal = new Date();
    if (weatherData && weatherData.local_time) {
        const parsed = new Date(weatherData.local_time);
        if (!isNaN(parsed.getTime())) {
            nowFinal = parsed;
        }
    }
    const hours = nowFinal.getHours();
    const minutes = nowFinal.getMinutes().toString().padStart(2, '0');
    const isPm = hours >= 12;
    const hours12 = (hours % 12) || 12;
    const timeStr = `${hours12}:${minutes} ${isPm ? 'PM' : 'AM'}`;
    if (greetingEl) {
        if (hours < 12) greetingEl.textContent = 'Good morning.';
        else if (hours < 18) greetingEl.textContent = 'Good afternoon.';
        else greetingEl.textContent = 'Good evening.';
    }
    if (timeWeatherEl) {
        if (weatherEnabled && weatherData) {
            const temp = weatherData.temp;
            const condition = weatherData.condition;
            const weatherStr = (temp === "N/A" || condition === "Weather unavailable")
                ? "Weather unavailable"
                : `${temp}°, ${condition}`;
            timeWeatherEl.textContent = `${timeStr} — ${weatherStr}`;
        } else {
            timeWeatherEl.textContent = timeStr;
        }
    }

    // Track opens
    const openCount = parseInt(localStorage.getItem('dashboardOpenCount') || '0');
    const newOpenCount = Math.min(openCount + 1, 3);
    localStorage.setItem('dashboardOpenCount', newOpenCount);

    // Send notifications for urgent assignments
    sendNotifications(allAssignments, config);

    // Footer links: show all items in-page
    if (viewAllAssignmentsLink) {
        viewAllAssignmentsLink.addEventListener('click', (e) => {
            e.preventDefault();
            showAllAssignments = true;
            renderAssignmentsFiltered(searchInput?.value || '', sortSelect?.value || 'due-asc');
            toggleBtn.textContent = 'Show less';
            assignmentsList.scrollIntoView({ behavior: 'smooth' });
        });
    }
    if (viewAllAnnouncementsLink) {
        viewAllAnnouncementsLink.addEventListener('click', (e) => {
            e.preventDefault();
            showAllAnnouncements = true;
            const list = showAllAnnouncements ? allAnnouncements : announcementsToShow;
            revealItems(announcementsList, list, formatAnnouncement);
            setupClickHandlers(announcementsList);
            announcementsList.scrollIntoView({ behavior: 'smooth' });
        });
    }

    console.log("Dashboard render complete");
}

// ===============================
// NOTIFICATIONS
// ===============================
async function sendNotifications(assignments, config) {
    if (!('Notification' in window)) return;
    
    // Check if user has enabled notifications
    if (Notification.permission === 'granted') {
        const now = new Date();
        const tomorrow = new Date(now);
        tomorrow.setDate(tomorrow.getDate() + 1);
        tomorrow.setHours(0, 0, 0, 0);
        
        // Notify about assignments due within 24 hours
        const urgent = assignments.filter(a => {
            if (!a.hasDueDate) return false;
            const dueDate = new Date(a.dueDate);
            return dueDate <= tomorrow && a.status === 'Not submitted';
        });
        
        if (urgent.length > 0) {
            const title = urgent.length === 1 
                ? `1 assignment due soon` 
                : `${urgent.length} assignments due soon`;
            const body = urgent.slice(0, 2).map(a => `${a.course}: ${a.shortName}`).join(', ');
            
            new Notification(title, {
                body: body,
                icon: '/favicon.ico',
                tag: 'assignment-urgent'
            });
        }

        // Canvas token expiration warning (7 days before)
        const expiration = config?.canvas?.token_expiration;
        if (expiration) {
            const expiryDate = new Date(expiration + 'T00:00:00');
            if (!isNaN(expiryDate.getTime())) {
                const msUntil = expiryDate.getTime() - now.getTime();
                const daysUntil = Math.ceil(msUntil / (1000 * 60 * 60 * 24));
                const notifyKey = `canvasTokenExpiryNotified:${expiration}`;
                if (daysUntil >= 0 && daysUntil <= 7 && !localStorage.getItem(notifyKey)) {
                    new Notification('Canvas token expiring soon', {
                        body: `Your Canvas API token expires in ${daysUntil} day${daysUntil === 1 ? '' : 's'}.`,
                        icon: '/favicon.ico',
                        tag: 'canvas-token-expiry'
                    });
                    localStorage.setItem(notifyKey, 'true');
                }
            }
        }
    } else if (Notification.permission !== 'denied') {
        Notification.requestPermission();
    }
}

// ===============================
// INIT
// ===============================
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', renderDashboard);
} else {
    renderDashboard();
}
