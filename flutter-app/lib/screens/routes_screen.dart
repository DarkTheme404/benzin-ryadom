import 'package:flutter/material.dart';
import '../config/theme.dart';
import '../models/route.dart';
import '../models/station.dart';
import '../services/api_service.dart';
import '../widgets/station_card.dart';
import 'station_detail_screen.dart';

class RoutesScreen extends StatefulWidget {
  const RoutesScreen({super.key});

  @override
  State<RoutesScreen> createState() => _RoutesScreenState();
}

class _RoutesScreenState extends State<RoutesScreen> {
  final ApiService _api = ApiService();
  final TextEditingController _searchController = TextEditingController();
  List<FuelRoute> _routes = [];
  List<Station> _routeStations = [];
  FuelRoute? _selectedRoute;
  bool _isLoading = false;

  @override
  void initState() {
    super.initState();
    _loadRoutes();
  }

  Future<void> _loadRoutes([String? query]) async {
    setState(() => _isLoading = true);
    try {
      final routes = await _api.getRoutes(query: query);
      setState(() {
        _routes = routes;
        _isLoading = false;
      });
    } catch (_) {
      setState(() => _isLoading = false);
    }
  }

  Future<void> _loadRouteStations(FuelRoute route) async {
    setState(() {
      _isLoading = true;
      _selectedRoute = route;
      _routeStations = [];
    });
    try {
      final stations = await _api.getRouteStations(route.id);
      setState(() {
        _routeStations = stations;
        _isLoading = false;
      });
    } catch (_) {
      setState(() => _isLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Маршруты'),
      ),
      body: Column(
        children: [
          _buildSearchBar(),
          Expanded(
            child: _selectedRoute != null
                ? _buildRouteStations()
                : _buildRouteList(),
          ),
        ],
      ),
    );
  }

  Widget _buildSearchBar() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
      child: TextField(
        controller: _searchController,
        style: const TextStyle(color: AppTheme.textPrimary),
        decoration: InputDecoration(
          hintText: 'Поиск маршрута (М-4, М-7...)',
          hintStyle: TextStyle(color: AppTheme.muted),
          prefixIcon: const Icon(Icons.search, color: AppTheme.muted),
          suffixIcon: _selectedRoute != null
              ? IconButton(
                  icon: const Icon(Icons.close, color: AppTheme.muted),
                  onPressed: () {
                    setState(() {
                      _selectedRoute = null;
                      _routeStations = [];
                    });
                    _searchController.clear();
                    _loadRoutes();
                  },
                )
              : null,
        ),
        onChanged: (q) {
          if (q.length >= 2) _loadRoutes(q);
        },
      ),
    );
  }

  Widget _buildRouteList() {
    if (_isLoading) {
      return const Center(
        child: CircularProgressIndicator(color: AppTheme.accent),
      );
    }

    if (_routes.isEmpty) {
      return Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(Icons.route_outlined,
                size: 64, color: AppTheme.muted.withValues(alpha: 0.3)),
            const SizedBox(height: 16),
            Text(
              'Найди АЗС на своём маршруте',
              style: TextStyle(color: AppTheme.muted, fontSize: 16),
            ),
            const SizedBox(height: 8),
            Text(
              'Выбери трассу или поищи по номеру',
              style: TextStyle(color: AppTheme.muted, fontSize: 13),
            ),
          ],
        ),
      );
    }

    return ListView.builder(
      padding: const EdgeInsets.all(16),
      itemCount: _routes.length,
      itemBuilder: (ctx, i) {
        final route = _routes[i];
        return Card(
          margin: const EdgeInsets.only(bottom: 8),
          child: ListTile(
            leading: Container(
              width: 48,
              height: 48,
              decoration: BoxDecoration(
                color: AppTheme.accent.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(10),
              ),
              child: const Icon(Icons.route, color: AppTheme.accent),
            ),
            title: Text(
              route.name,
              style: const TextStyle(
                color: AppTheme.textPrimary,
                fontWeight: FontWeight.w600,
              ),
            ),
            subtitle: Text(
              '${route.stationCount ?? '?'} АЗС',
              style: const TextStyle(color: AppTheme.muted, fontSize: 13),
            ),
            trailing: const Icon(Icons.chevron_right, color: AppTheme.muted),
            onTap: () => _loadRouteStations(route),
          ),
        );
      },
    );
  }

  Widget _buildRouteStations() {
    if (_isLoading) {
      return const Center(
        child: CircularProgressIndicator(color: AppTheme.accent),
      );
    }

    return ListView.builder(
      padding: const EdgeInsets.all(16),
      itemCount: _routeStations.length,
      itemBuilder: (ctx, i) {
        final station = _routeStations[i];
        return StationCard(
          station: station,
          selectedFuel: '95',
          onTap: () => Navigator.push(
            ctx,
            MaterialPageRoute(
              builder: (_) => StationDetailScreen(stationId: station.id),
            ),
          ),
        );
      },
    );
  }
}
