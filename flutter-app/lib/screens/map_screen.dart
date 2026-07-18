import 'dart:async';
import 'package:flutter/material.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart';
import '../config/theme.dart';
import '../models/station.dart';
import '../services/api_service.dart';
import '../services/location_service.dart';
import '../widgets/station_bottom_sheet.dart';

class MapScreen extends StatefulWidget {
  const MapScreen({super.key});

  @override
  State<MapScreen> createState() => _MapScreenState();
}

class _MapScreenState extends State<MapScreen> {
  final Completer<GoogleMapController> _mapController = Completer();
  final LocationService _locationService = LocationService();
  final ApiService _api = ApiService();
  final Set<Marker> _markers = {};
  List<Station> _stations = [];
  bool _isLoading = true;
  String _selectedFuel = '95';
  Station? _selectedStation;
  bool _showSheet = false;

  static const LatLng _defaultCenter = LatLng(56.8587, 40.9957);

  @override
  void initState() {
    super.initState();
    _initLocation();
  }

  Future<void> _initLocation() async {
    final pos = await _locationService.getCurrentPosition();
    if (pos != null) {
      _loadStations(pos.latitude, pos.longitude);
    } else {
      _loadStations(_defaultCenter.latitude, _defaultCenter.longitude);
    }
  }

  Future<void> _loadStations(double lat, double lon) async {
    setState(() {
      _isLoading = true;
    });

    try {
      final stations = await _api.getStations(
        lat: lat,
        lon: lon,
        fuel: _selectedFuel,
      );
      setState(() {
        _stations = stations;
        _isLoading = false;
        _buildMarkers();
      });
    } catch (e) {
      setState(() {
        _isLoading = false;
      });
    }
  }

  void _buildMarkers() {
    _markers.clear();
    for (final station in _stations) {
      if (station.lat == null || station.lon == null) continue;

      final color = _getMarkerColor(station.fuelStatus);

      _markers.add(
        Marker(
          markerId: MarkerId('station_${station.id}'),
          position: LatLng(station.lat!, station.lon!),
          icon: BitmapDescriptor.defaultMarkerWithHue(color),
          onTap: () => _onStationTap(station),
          infoWindow: InfoWindow(
            title: station.name,
            snippet: station.mainPrice ?? 'Нет данных',
          ),
        ),
      );
    }
  }

  double _getMarkerColor(String status) {
    switch (status) {
      case 'in_stock':
        return BitmapDescriptor.hueGreen;
      case 'partial':
        return BitmapDescriptor.hueYellow;
      case 'out_of_stock':
        return BitmapDescriptor.hueRed;
      default:
        return BitmapDescriptor.hueAzure;
    }
  }

  void _onStationTap(Station station) {
    setState(() {
      _selectedStation = station;
      _showSheet = true;
    });
  }

  void _onFuelChanged(String fuel) {
    setState(() => _selectedFuel = fuel);
    _initLocation();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Stack(
        children: [
          GoogleMap(
            initialCameraPosition: CameraPosition(
              target: _defaultCenter,
              zoom: 12,
            ),
            onMapCreated: (controller) {
              if (!_mapController.isCompleted) {
                _mapController.complete(controller);
              }
            },
            markers: _markers,
            myLocationEnabled: true,
            myLocationButtonEnabled: false,
            mapToolbarEnabled: false,
            zoomControlsEnabled: false,
            onCameraIdle: () async {
              final controller = await _mapController.future;
              final bounds = await controller.getVisibleRegion();
              final center = LatLng(
                (bounds.southwest.latitude + bounds.northeast.latitude) / 2,
                (bounds.southwest.longitude + bounds.northeast.longitude) / 2,
              );
              _loadStations(center.latitude, center.longitude);
            },
          ),
          if (_isLoading)
            const Positioned(
              top: 0,
              left: 0,
              right: 0,
              child: LinearProgressIndicator(
                backgroundColor: Colors.transparent,
                valueColor: AlwaysStoppedAnimation(AppTheme.accent),
              ),
            ),
          Positioned(
            top: MediaQuery.of(context).padding.top + 8,
            left: 16,
            right: 16,
            child: _buildFuelChips(),
          ),
          Positioned(
            bottom: 16,
            right: 16,
            child: _buildLocationButton(),
          ),
          if (_showSheet && _selectedStation != null)
            Positioned(
              bottom: 0,
              left: 0,
              right: 0,
              child: StationBottomSheet(
                station: _selectedStation!,
                onClose: () => setState(() {
                  _showSheet = false;
                  _selectedStation = null;
                }),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildFuelChips() {
    return Container(
      padding: const EdgeInsets.all(4),
      decoration: BoxDecoration(
        color: AppTheme.bg,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppTheme.divider),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          _fuelChip('92', 'АИ-92'),
          _fuelChip('95', 'АИ-95'),
          _fuelChip('98', 'АИ-98'),
          _fuelChip('diesel', 'ДТ'),
          _fuelChip('lpg', 'Газ'),
        ],
      ),
    );
  }

  Widget _fuelChip(String value, String label) {
    final isSelected = _selectedFuel == value;
    return GestureDetector(
      onTap: () => _onFuelChanged(value),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(
          color: isSelected ? AppTheme.accent : Colors.transparent,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Text(
          label,
          style: TextStyle(
            color: isSelected ? Colors.white : AppTheme.textSecondary,
            fontSize: 12,
            fontWeight: isSelected ? FontWeight.w600 : FontWeight.w400,
          ),
        ),
      ),
    );
  }

  Widget _buildLocationButton() {
    return FloatingActionButton(
      mini: true,
      backgroundColor: AppTheme.bgCard,
      onPressed: () async {
        final pos = await _locationService.getCurrentPosition();
        if (pos != null) {
          final controller = await _mapController.future;
          controller.animateCamera(
            CameraUpdate.newLatLngZoom(
              LatLng(pos.latitude, pos.longitude),
              14,
            ),
          );
        }
      },
      child: const Icon(Icons.my_location, color: AppTheme.accent),
    );
  }
}
