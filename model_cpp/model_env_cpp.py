import numpy as np
import matplotlib.pyplot as plt
import matplotlib
import random
import matplotlib.colors as mcol
import cv2 #Requires the package opencv-python
import math
try:
    import cppCellModel
except:
    strErr = "\n\n`cppCellModel` module not found, "
    raise RuntimeError(strErr)

from deer.base_classes import Environment


class CellEnvironment(Environment):
    """Environment that the reinforcement learning agent uses to interact with the simulation."""

    def __init__(self, obs_type, resize, reward, action_type, special_reward):
        """Constructor of the environment

        Parameters:
        obs_type : Type of observations provided to the agent ('head' for segmentation or 'types' for weighted sums)
        resize : True if the observations should be resized to 25 * 25 arrays
        reward : Type of reward function used ('dose' to minimize the total dose, 'killed' to maximize damage to cancer
                 cells while miniizing damage to healthy tissue and 'oar' to minimize damage to the Organ At Risk
        action_type : 'DQN' means that we have a discrete action domain and 'DDPG' means that it is continuous
        special_reward : True if the agent should receive a special reward at the end of the episode.
        """
        self.controller_capsule = cppCellModel.controller_constructor(50, 50, 100, 350)
        self.init_hcell_count = cppCellModel.HCellCount()
        self.obs_type = obs_type
        self.resize = resize
        self.reward = reward
        self.action_type = action_type
        self.special_reward = special_reward
        self.dose_map = None
        self.dataset = None

    def get_tick(self):
        return cppCellModel.controllerTick(self.controller_capsule)

    def init_dose_map(self):
        self.dose_map = np.zeros((50, 50), dtype=float)
        self.dose_maps = []
        self.tumor_images = []

    def init_dataset(self):
        self.dataset = [[], [], []]

    def add_radiation(self, dose, radius, center_x, center_y):
        if dose == 0:
            return
        multiplicator = get_multiplicator(dose, radius)
        for x in range(50):
            for y in range(50):
                dist = math.sqrt((center_x - x)**2 + (center_y - y)**2)
                self.dose_map[x, y] += scale(radius, dist, multiplicator)

    def show_dose_map(self):
        pos = plt.imshow(self.dose_map, cmap=mcol.LinearSegmentedColormap.from_list("MyCmapName",[[0,0,0.6],"r"]))
        plt.colorbar(pos)
        plt.show()

    def reset(self, mode):
        #cppCellModel.delete_controller(self.controller_capsule)
        self.controller_capsule = cppCellModel.controller_constructor(50, 50, 100, 350)
        self.init_hcell_count = cppCellModel.HCellCount()
        if mode == -1:
            self.verbose = False
        else :
            self.verbose = True
        self.total_dose = 0
        self.num_doses = 0
        self.radiation_h_killed = 0
        if self.dose_map is not None:
            self.dose_maps.append((cppCellModel.controllerTick(self.controller_capsule) - 350, np.copy(self.dose_map)))
            self.tumor_images.append((cppCellModel.controllerTick(self.controller_capsule) - 350,
                                      cppCellModel.observeDensity(self.controller_capsule)))
        return self.observe()
    
    def act(self, action):
        dose = 1 + action / 2 if self.action_type == 'DQN' else action[0] * 4 + 1
        rest = 24 if self.action_type == 'DQN' else int(round(action[1] * 60 + 12))
        if self.dose_map is not None:
            tumor_radius = cppCellModel.tumor_radius(self.controller_capsule)
        pre_hcell = cppCellModel.HCellCount()
        pre_ccell = cppCellModel.CCellCount()
        self.total_dose += dose
        self.num_doses += 1 if dose > 0 else 0
        cppCellModel.irradiate(self.controller_capsule, dose)
        self.radiation_h_killed += (pre_hcell - cppCellModel.HCellCount())
        if self.dataset is not None:
            self.dataset[0].append(cppCellModel.controllerTick(self.controller_capsule) - 350)
            self.dataset[1].append((pre_ccell, cppCellModel.CCellCount()))
            self.dataset[2].append(dose)
        if self.dose_map is not None:
            self.add_radiation(dose, tumor_radius, cppCellModel.get_center_x(self.controller_capsule), cppCellModel.get_center_y(self.controller_capsule))
            self.dose_maps.append((cppCellModel.controllerTick(self.controller_capsule) - 350, np.copy(self.dose_map)))
            self.tumor_images.append((cppCellModel.controllerTick(self.controller_capsule) - 350, cppCellModel.observeDensity(self.controller_capsule)))
        p_hcell = cppCellModel.HCellCount()
        p_ccell = cppCellModel.CCellCount()
        cppCellModel.go(self.controller_capsule, rest)
        post_hcell = cppCellModel.HCellCount()
        post_ccell = cppCellModel.CCellCount()
        reward = self.adjust_reward(dose, pre_ccell - post_ccell, pre_hcell-min(post_hcell, p_hcell))
        if self.verbose:
                print("Radiation dose :", dose, "Gy ", "remaining :", post_ccell,  "time =", rest, "reward=", reward)
        return reward

    def surviving_fraction(self):
        return cppCellModel.HCellCount() / self.init_hcell_count

    def adjust_reward(self, dose, ccell_killed, hcells_lost):
        if self.special_reward and self.inTerminalState():
            if self.end_type == "L" or self.end_type == "T":
                return -1
            else:
                if self.reward == 'dose':
                    return  - dose / 200 + 0.5 - (self.init_hcell_count - cppCellModel.HCellCount()) / 3000
                else:
                    return 0.5 - (self.init_hcell_count - cppCellModel.HCellCount()) / 3000#(cppCellModel.HCellCount() / self.init_hcell_count) - 0.5 - (2 * hcells_lost/2500)
        else:
            if self.reward == 'dose' or self.reward == 'oar':
                return - dose / 200 + (ccell_killed - 5 * hcells_lost)/100000
            elif self.reward == 'killed':
                return (ccell_killed - 5 * hcells_lost)/100000

    def inTerminalState(self):
        if cppCellModel.CCellCount() <= 0 :
            if self.verbose:
                print("No more cancer")
            self.end_type = 'W'
            return True
        elif cppCellModel.HCellCount() < 10:
            if self.verbose:
                print("Cancer wins")
            self.end_type = "L"
            return True
        elif cppCellModel.controllerTick(self.controller_capsule) > 1550:
            if self.verbose:
                print("Time out!")
            self.end_type = "T"
            return True
        else:
            return False

    def nActions(self):
        if self.action_type == 'DQN':
            return 9
        elif self.action_type == 'DDPG':
            return [[0, 1], [0, 1]]

 
    def end(self):
        cppCellModel.delete_controller(self.controller_capsule)

    def inputDimensions(self):
        if self.obs_type == 'scalars':
            tab = [(1,), (1,), (1,)]
        elif self.resize:
            tab = [(1, 25, 25)]
        else:
            tab = [(1, 50, 50)]
        return tab

    def observe(self):
        if self.obs_type == 'scalars':
            return [cppCellModel.controllerTick(self.controller_capsule) / 2000, cppCellModel.HCellCount() / 100000, cppCellModel.CCellCount()/ 50000]
        else:
            if self.obs_type == 'densities':
                cells = (np.array(cppCellModel.observeDensity(self.controller_capsule), dtype=np.float32)) / 100.0
            else:
                cells = (np.array(cppCellModel.observeSegmentation(self.controller_capsule), dtype=np.float32) + 1.0) / 2.0 #  Obs from 0 to 1
            if self.resize:
                cells = cv2.resize(cells, dsize=(25,25), interpolation=cv2.INTER_CUBIC)
            return [cells]

    def summarizePerformance(self, test_data_set, *args, **kwargs):
        print(test_data_set)

def transform(head):
    to_ret = np.zeros(shape=(head.shape[0], head.shape[1], 3), dtype=np.int)
    for i in range(head.shape[0]):
        for j in range(head.shape[1]):
            if head[i][j] == 1:
                to_ret[i][j][1] = 120
            elif head[i][j] == -1:
                to_ret[i][j][0] = 120
    return to_ret

def transform_densities(obs):
    to_ret = np.zeros(shape=(obs.shape[0], obs.shape[1], 3), dtype=int)
    for i in range(obs.shape[0]):
        for j in range(obs.shape[1]):
            if obs[i][j] < 0:
                to_ret[i][j][0] = 60 + min(- obs[i][j] * 4, 195)
            elif obs[i][j] > 0:
                to_ret[i][j][1] = 60 + min(obs[i][j] * 8, 195)
    return to_ret


def conv(rad, x):
    denom = 3.8 # //sqrt(2) * 2.7
    return math.erf((rad - x)/denom) - math.erf((-rad - x) / denom)


def get_multiplicator(dose, radius):
    return dose / conv(14, 0)


def scale(radius, x, multiplicator):
    return multiplicator * conv(14, x * 10 / radius)


def tcp_test(num):
    count_failed = 0
    count_success = 0
    steps = []
    counts = []
    for i in range(num):
        print(i)
        controller = cppCellModel.controller_constructor(50, 50, 100, 350)
        counts.append(cppCellModel.HCellCount())
        for i in range(35):
            #print("Before", cppCellModel.HCellCount(), cppCellModel.CCellCount())
            cppCellModel.irradiate(controller, 2)
            ##print("After", cppCellModel.HCellCount(), cppCellModel.CCellCount())
            cppCellModel.go(controller, 24)
            if cppCellModel.CCellCount() == 0:
                steps.append(i + 1)
                break
        count = cppCellModel.CCellCount()
        if count > 10:
            count_failed += 1
        elif count == 0:
            count_success += 1
        counts[-1] /= cppCellModel.HCellCount()
        #cppCellModel.delete_controller(controller)
    print("Percentage of full recovs :", (100*count_success)/ num)
    print("Percentage of almost recovs :", (100*(num - count_failed))/ num)
    print("Average dose in successes :", 2*sum(steps)/len(steps))
    print(sum(counts) / len(counts))


def _test():
    for i in range(5):
        controller = cppCellModel.controller_constructor(50, 50, 100, 350)
        cppCellModel.irradiate(controller, 2)
        cppCellModel.go(controller, 24)
        print(cppCellModel.CCellCount())
        #cppCellModel.delete_controller(controller)

def save_tumor_image(data, tick):
    sizes = np.shape(data)
    fig = plt.figure()
    fig.set_size_inches(1. * sizes[0] / sizes[1], 1, forward = False)
    ax = plt.Axes(fig, [0., 0., 1., 1.])
    ax.set_axis_off()
    fig.add_axes(ax)
    ax.imshow(data)
    plt.savefig('tmp/t'+str(tick), dpi=500)
    plt.close()

if __name__ == '__main__':
    #tcp_test(14)
    ticks = []
    cancer_cells = []

    controller = cppCellModel.controller_constructor(50, 50, 100, 0)
    cppCellModel.go(controller, 350)
    for i in range(35):
        a = cppCellModel.CCellCount()
        cppCellModel.irradiate(controller, 2)
        b = cppCellModel.CCellCount()
        cppCellModel.go(controller, 24)
        c= cppCellModel.CCellCount()
        print(b / a, c / b)
    #cppCellModel.delete_controller(controller)
    '''
    save_tumor_image(transform_densities(cppCellModel.observeGrid(controller)), 0)
    for i in range(50):
        ticks.append(cppCellModel.controllerTick(controller))
        cancer_cells.append(cppCellModel.CCellCount())
        cppCellModel.go(controller, 1)
    save_tumor_image(transform_densities(cppCellModel.observeGrid(controller)), 50)
    for i in range(50):
        ticks.append(cppCellModel.controllerTick(controller))
        cancer_cells.append(cppCellModel.CCellCount())
        cppCellModel.go(controller, 1)
    save_tumor_image(transform_densities(cppCellModel.observeGrid(controller)), 100)
    for i in range(50):
        ticks.append(cppCellModel.controllerTick(controller))
        cancer_cells.append(cppCellModel.CCellCount())
        cppCellModel.go(controller, 1)
    save_tumor_image(transform_densities(cppCellModel.observeGrid(controller)), 150)
    for i in range(50):
        ticks.append(cppCellModel.controllerTick(controller))
        cancer_cells.append(cppCellModel.CCellCount())
        cppCellModel.go(controller, 1)
    save_tumor_image(transform_densities(cppCellModel.observeGrid(controller)), 200)
    for i in range(50):
        ticks.append(cppCellModel.controllerTick(controller))
        cancer_cells.append(cppCellModel.CCellCount())
        cppCellModel.go(controller, 1)
    save_tumor_image(transform_densities(cppCellModel.observeGrid(controller)), 250)
    for i in range(50):
        ticks.append(cppCellModel.controllerTick(controller))
        cancer_cells.append(cppCellModel.CCellCount())
        cppCellModel.go(controller, 1)
    save_tumor_image(transform_densities(cppCellModel.observeGrid(controller)), 300)
    for i in range(50):
        ticks.append(cppCellModel.controllerTick(controller))
        cancer_cells.append(cppCellModel.CCellCount())
        cppCellModel.go(controller, 1)
    '''
    #cppCellModel.go(controller, 550)
    #save_tumor_image(transform_densities(cppCellModel.observeGrid(controller)), 551)
    #cppCellModel.delete_controller(controller)
    '''
    plt.plot(ticks, cancer_cells)
    plt.xlabel("Hours")
    plt.ylabel("Number of cancer cells")

    plt.savefig('tmp/ccellstart')
    tcp_test(100)
    import matplotlib.pyplot as plt
    import matplotlib
    matplotlib.use("TkAgg")
    plt.ion()

    controller = cppCellModel.controller_constructor(50,50,100,350)

    fig, axs = plt.subplots(2,2, constrained_layout=True)
    fig.suptitle('Cell proliferation at t = 0')
    glucose_plot = axs[0][0]
    glucose_plot.set_title('Glucose density')
    cell_plot = axs[1][0]
    cell_plot.set_title('Types of cells')
    oxygen_plot = axs[0][1]
    oxygen_plot.set_title('Oxygen density')
    cancer_count_plot = axs[1][1]
    cancer_count_plot.set_title('Number of cancer cells')
    ccount_ticks = []
    ccount_vals = []

    glucose_plot.imshow(cppCellModel.observeGlucose(controller))
    oxygen_plot.imshow(cppCellModel.observeOxygen(controller))
    cell_plot.imshow(cppCellModel.observeGrid(controller))
    ccount_ticks.append(cppCellModel.controllerTick(controller))
    ccount_vals.append(cppCellModel.CCellCount())
    cancer_count_plot.plot(ccount_ticks, ccount_vals)
    print(cppCellModel.CCellCount())
    for i in range(200):
        cppCellModel.go(controller, 12)
        if i % 2 == 0:
            cppCellModel.irradiate(controller, 2.0)
        fig.suptitle('Cell proliferation at t = ' + str((i+1)*12))
        glucose_plot.imshow(cppCellModel.observeGlucose(controller))
        oxygen_plot.imshow(cppCellModel.observeOxygen(controller))
        cell_plot.imshow(transform(cppCellModel.observeType(controller)))
        ccount_ticks.append(cppCellModel.controllerTick(controller))
        ccount_vals.append(cppCellModel.CCellCount())
        cancer_count_plot.plot(ccount_ticks, ccount_vals)
        plt.pause(0.02)

    cppCellModel.delete_controller(controller)
    '''
